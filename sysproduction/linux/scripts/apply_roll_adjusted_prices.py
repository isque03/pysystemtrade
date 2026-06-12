#!/usr/bin/env python3
"""
Fix adjusted prices for SILVER and COPPER-micro by retroactively applying
the roll using actual contract price data (no forward-fill of gaps).

These instruments had their PRICE contract expire without IB gateway recording
final prices. The multiple prices have NaN in the PRICE column for the gap
period. We correct this by:
1. Truncating the multiple prices at the last real PRICE date
2. Adding a proper roll transition using the FORWARD contract's real prices
3. Stitching adjusted prices from the corrected multiple prices
"""
import sys
sys.path.insert(0, '/pysystemtrade')
sys.path.insert(0, '/pysystemtrade/private')

import pandas as pd
import numpy as np
from copy import copy

from sysdata.data_blob import dataBlob
from sysproduction.data.positions import updatePositions
from sysproduction.data.prices import diagPrices, updatePrices
from sysproduction.data.contracts import dataContracts
from sysobjects.adjusted_prices import futuresAdjustedPrices
from sysobjects.multiple_prices import futuresMultiplePrices
from sysobjects.contracts import futuresContract
from sysobjects.futures_per_contract_prices import futuresContractPrices
from sysobjects.production.roll_state import default_state
from syscore.constants import success, failure


def build_corrected_multiple_prices(data, instrument_code, old_contract, new_contract,
                                    next_contract=None):
    """
    Build corrected multiple prices for an instrument where the old PRICE contract
    expired without a proper roll transition.

    Steps:
    1. Get existing multiple prices up to the last real PRICE date
    2. Get new contract (FORWARD) hourly prices
    3. Add a roll transition row at the last real PRICE date
    4. Append new contract prices
    """
    diag = diagPrices(data)
    mult = diag.get_multiple_prices(instrument_code)

    # Find all rows where PRICE_CONTRACT == old_contract and PRICE is not NaN
    # PRICE_CONTRACT is stored as strings in pysystemtrade parquet files
    old_mask = (mult['PRICE_CONTRACT'].astype(str) == str(old_contract)) & mult['PRICE'].notna()
    if not old_mask.any():
        print(f"  ERROR: No valid prices found for {instrument_code}/{old_contract}")
        return None

    last_real_idx = mult[old_mask].index[-1]
    last_real_row = mult.loc[last_real_idx]
    print(f"  Last real PRICE ({old_contract}): {last_real_idx} = {last_real_row['PRICE']:.4f}")

    # Truncate at last real price date (inclusive)
    mult_truncated = mult[mult.index <= last_real_idx].copy()

    # Get new contract (FORWARD) prices
    new_contract_obj = futuresContract(instrument_code, str(new_contract))
    try:
        new_prices = diag.get_merged_prices_for_contract_object(new_contract_obj)
        new_prices_final = new_prices.return_final_prices()
    except Exception as e:
        print(f"  ERROR getting prices for {instrument_code}/{new_contract}: {e}")
        return None

    # Get the FORWARD price at the roll point
    forward_at_roll = last_real_row['FORWARD']
    if pd.isna(forward_at_roll):
        # Try to get it from the contract prices directly
        try:
            fwd_at_roll = new_prices_final.loc[:last_real_idx]
            if len(fwd_at_roll) > 0:
                forward_at_roll = float(fwd_at_roll.iloc[-1])
            else:
                print(f"  WARNING: No FORWARD price at roll point, using last available")
                forward_at_roll = float(new_prices_final.iloc[0])
        except:
            print(f"  ERROR: Cannot determine FORWARD price at roll point")
            return None

    old_price_at_roll = float(last_real_row['PRICE'])
    print(f"  Roll: {old_contract}@{old_price_at_roll:.4f} → {new_contract}@{forward_at_roll:.4f}")
    print(f"  Roll differential: {forward_at_roll - old_price_at_roll:.4f}")

    # Get next contract for FORWARD column
    if next_contract is not None:
        next_contract_obj = futuresContract(instrument_code, str(next_contract))
        try:
            next_prices_raw = diag.get_merged_prices_for_contract_object(next_contract_obj)
            next_prices_final = next_prices_raw.return_final_prices()
        except Exception as e:
            print(f"  WARNING: Cannot get next contract {next_contract} prices: {e}, FORWARD will be NaN")
            next_prices_final = pd.Series(dtype=float)
    else:
        next_prices_final = pd.Series(dtype=float)

    # Build new rows for the period after last_real_idx
    # Start with a roll transition row (last old price + first new price, +1 second)
    roll_ts = last_real_idx + pd.Timedelta(seconds=1)
    roll_ts2 = last_real_idx + pd.Timedelta(seconds=2)

    new_rows = []

    # Row 1: overlap row (both old and new contract prices at roll point)
    # PRICE = old contract last price, FORWARD = new contract price at that time
    # Use string contract codes to match the existing parquet storage format
    next_code = str(next_contract) if next_contract is not None else str(new_contract)
    row1 = {
        'PRICE_CONTRACT': str(old_contract),
        'PRICE': old_price_at_roll,
        'FORWARD_CONTRACT': str(new_contract),
        'FORWARD': forward_at_roll,
        'CARRY_CONTRACT': str(old_contract),
        'CARRY': old_price_at_roll,
    }

    # Row 2: the actual roll - PRICE becomes new contract
    # Find new contract's first price after the roll
    new_price_start = new_prices_final[new_prices_final.index > last_real_idx]
    if len(new_price_start) == 0:
        new_price_at_roll = forward_at_roll
    else:
        new_price_at_roll = float(new_price_start.iloc[0])

    row2 = {
        'PRICE_CONTRACT': str(new_contract),
        'PRICE': new_price_at_roll,
        'FORWARD_CONTRACT': next_code,
        'FORWARD': np.nan,
        'CARRY_CONTRACT': str(new_contract),
        'CARRY': new_price_at_roll,
    }

    # Combine truncated multiple prices + new rows + subsequent new contract prices
    new_rows_df = pd.DataFrame([row1, row2], index=[roll_ts, roll_ts2])

    # Get subsequent new contract price rows (hourly, after roll_ts2)
    subsequent = new_prices_final[new_prices_final.index > roll_ts2].copy()
    if len(subsequent) > 0:
        # Build forward prices for this period
        if len(next_prices_final) > 0:
            fwd_aligned = next_prices_final.reindex(subsequent.index)
        else:
            fwd_aligned = pd.Series(np.nan, index=subsequent.index)

        subseq_df = pd.DataFrame({
            'PRICE_CONTRACT': str(new_contract),
            'PRICE': subsequent.values,
            'FORWARD_CONTRACT': next_code,
            'FORWARD': fwd_aligned.values,
            'CARRY_CONTRACT': str(new_contract),
            'CARRY': subsequent.values,
        }, index=subsequent.index)
    else:
        subseq_df = pd.DataFrame()

    # Concatenate all parts
    parts = [mult_truncated, new_rows_df]
    if len(subseq_df) > 0:
        parts.append(subseq_df)

    combined = pd.concat(parts)
    combined = combined.sort_index()
    combined = combined[~combined.index.duplicated(keep='last')]

    print(f"  Combined multiple prices: {len(combined)} rows, last={combined.index[-1]}")

    return futuresMultiplePrices(combined)


INSTRUMENTS = {
    "COPPER-micro": {
        "old_contract": "20260600",  # June: last PRICE_CONTRACT, data through May 26
        "new_contract": "20260700",  # July: data through today
        "next_contract": "20260800",  # August: data through today
    },
    "VIX_mini": {
        "old_contract": "20260400",  # April: last PRICE_CONTRACT, data through Apr 14
        "new_contract": "20260600",  # June: data through today
        "next_contract": "20260700",  # July: empty, FORWARD will be NaN
    },
    "ETHER-micro": {
        "old_contract": "20260300",  # March: last PRICE_CONTRACT, data through Mar 26
        "new_contract": "20260600",  # June: 403 rows just fetched
        "next_contract": "20260900",  # September: 247 rows just fetched
    },
    "BITCOIN": {
        "old_contract": "20260300",  # March: last PRICE_CONTRACT, data through Mar 26
        "new_contract": "20260600",  # June: 402 rows just fetched
        "next_contract": "20260900",  # September: just fetched
    },
}

results = {}

with dataBlob(log_name="retroactive-roll") as data:
    update_positions = updatePositions(data)
    price_updater = updatePrices(data)

    for instrument_code, cfg in INSTRUMENTS.items():
        print(f"\n{'='*60}")
        print(f"Retroactive roll for: {instrument_code}")
        print(f"{'='*60}")
        try:
            corrected_mult = build_corrected_multiple_prices(
                data, instrument_code,
                old_contract=cfg["old_contract"],
                new_contract=cfg["new_contract"],
                next_contract=cfg.get("next_contract"),
            )
            if corrected_mult is None:
                results[instrument_code] = "FAILURE: could not build corrected multiple prices"
                continue

            # Stitch adjusted prices from corrected multiple prices
            new_adj = futuresAdjustedPrices.stitch_multiple_prices(corrected_mult)
            nan_count = new_adj.isna().sum()
            print(f"  New adjusted prices: {len(new_adj)} rows, last={new_adj.index[-1]}, NaN={nan_count}")

            if nan_count > 100:
                print(f"  WARNING: {nan_count} NaN values in adjusted prices — check the roll")

            # Write corrected multiple prices
            price_updater.add_multiple_prices(
                instrument_code, corrected_mult, ignore_duplication=True
            )
            # Write new adjusted prices
            price_updater.add_adjusted_prices(
                instrument_code, new_adj, ignore_duplication=True
            )
            # Set roll state back to No_Roll
            update_positions.set_roll_state(instrument_code, default_state)

            print(f"  ✓ {instrument_code}: retroactive roll succeeded → No_Roll")
            results[instrument_code] = "SUCCESS"

        except Exception as e:
            import traceback
            print(f"  ✗ {instrument_code}: exception: {e}")
            traceback.print_exc()
            results[instrument_code] = f"ERROR: {e}"

print("\n" + "="*60)
print("SUMMARY")
print("="*60)
for inst, status in results.items():
    print(f"  {inst}: {status}")
