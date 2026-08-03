#!/usr/bin/env python3
"""
Programmatic roll for instruments whose PRICE_CONTRACT has gone stale.

Uses pysystemtrade's own rollingAdjustedAndMultiplePrices class (the same
code invoked by interactive_update_roll_status) — but without the interactive
confirmation prompt or the forward-fill fallback prompt.

Roll failures are persisted in MongoDB collection 'auto_roll_issues' (database
'production') — completely separate from pysystemtrade's own collections.
Pending issues are retried on each run. If the FORWARD contract is missing,
this script force-fetches it from IB before retrying.

Cron placement: after run_daily_price_updates, before run_daily_update_multiple_adjusted_prices
"""
import sys
sys.path.insert(0, '/pysystemtrade')
sys.path.insert(0, '/pysystemtrade/private')

import datetime
import pandas as pd
import pymongo

from syscore.constants import success, failure
from sysdata.data_blob import dataBlob
from sysdata.mongodb.mongo_connection import mongoDb
from sysproduction.data.prices import diagPrices, updatePrices
from sysproduction.data.positions import updatePositions
from sysproduction.data.contracts import dataContracts
from sysproduction.reporting.data.rolls import rollingAdjustedAndMultiplePrices
from sysproduction.update_historical_prices import (
    update_historical_prices_for_instrument_and_contract,
    get_config_for_price_filtering,
)
from sysobjects.contracts import futuresContract
from sysobjects.production.roll_state import roll_adj_state, default_state

STALE_DAYS_THRESHOLD = 5
ROLL_ISSUES_COLLECTION = "auto_roll_issues"

# Instruments permanently excluded from auto-roll (discontinued or not traded).
# Add instrument codes here to suppress retries and issue tracking.
EXCLUDED_INSTRUMENTS = {
    "EDOLLAR",     # discontinued (replaced by SOFR/SR3 in 2023)
    "US2Y_micro",  # not traded — insufficient volume
}


class RollIssueStore:
    """
    Thin wrapper around MongoDB collection 'auto_roll_issues'.
    Uses the same Atlas connection as pysystemtrade (reads from private config).
    Collection is ours alone — no conflict with pysystemtrade internals.
    """

    def __init__(self):
        mdb = mongoDb()
        client = pymongo.MongoClient(mdb.host)
        db = client[mdb.database_name]
        self._col = db[ROLL_ISSUES_COLLECTION]
        # Index on instrument_code for fast lookups (idempotent)
        self._col.create_index("instrument_code", unique=True, background=True)

    def record_failure(self, instrument_code, price_contract, forward_contract, error_msg):
        now = datetime.datetime.utcnow()
        self._col.update_one(
            {"instrument_code": instrument_code},
            {
                "$set": {
                    "price_contract": price_contract,
                    "forward_contract": forward_contract,
                    "last_attempt": now,
                    "last_error": str(error_msg),
                    "status": "pending",
                },
                "$setOnInsert": {"first_detected": now},
                "$inc": {"attempt_count": 1},
            },
            upsert=True,
        )

    def record_success(self, instrument_code):
        now = datetime.datetime.utcnow()
        self._col.update_one(
            {"instrument_code": instrument_code},
            {
                "$set": {
                    "status": "resolved",
                    "resolved_at": now,
                    "last_attempt": now,
                }
            },
        )

    def mark_manual_required(self, instrument_code, error_msg):
        now = datetime.datetime.utcnow()
        self._col.update_one(
            {"instrument_code": instrument_code},
            {
                "$set": {
                    "status": "manual_required",
                    "last_attempt": now,
                    "last_error": str(error_msg),
                }
            },
        )

    def pending_instruments(self):
        """Return list of instrument_codes with status='pending'."""
        docs = self._col.find({"status": "pending"}, {"instrument_code": 1})
        return [d["instrument_code"] for d in docs]

    def all_issues(self):
        return list(self._col.find({}, {"_id": 0}))


def _last_real_price_date(diag, instrument_code, contract_date_str):
    """Last date with real (non-empty) raw per-contract price data, or None."""
    try:
        contract_obj = futuresContract(instrument_code, contract_date_str)
        raw_prices = diag.get_merged_prices_for_contract_object(contract_obj).return_final_prices()
        if len(raw_prices) > 0:
            return raw_prices.index[-1].date()
    except Exception:
        pass
    return None


def _attempt_roll(data, instrument_code):
    """Try rollingAdjustedAndMultiplePrices. Returns (rolling_obj or None, exception or None)."""
    try:
        rolling_obj = rollingAdjustedAndMultiplePrices(
            data, instrument_code, allow_forward_fill=False
        )
        _ = rolling_obj.updated_multiple_prices
        _ = rolling_obj.new_adjusted_prices
        return rolling_obj, None
    except Exception as e:
        return None, e


def _force_fetch_contract(data, instrument_code, contract_date_str):
    """
    Register contract in MongoDB, mark as sampling, force-fetch IB prices.
    Returns True if prices were successfully fetched, False otherwise.
    """
    dc = dataContracts(data)
    cleaning_config = get_config_for_price_filtering(data)
    c = futuresContract(instrument_code, contract_date_str)

    try:
        if not dc.is_contract_in_data(c):
            data.log.warning(
                f"{instrument_code}/{contract_date_str}: not in MongoDB, adding..."
            )
            dc.add_contract_data(c, ignore_duplication=True)

        dc.mark_contract_as_sampling(c)
        data.log.warning(
            f"{instrument_code}/{contract_date_str}: marked as sampling, force-fetching prices..."
        )

        update_historical_prices_for_instrument_and_contract(
            c, data, cleaning_config=cleaning_config
        )

        diag = diagPrices(data)
        prices = diag.get_merged_prices_for_contract_object(c).return_final_prices()
        if len(prices) == 0:
            data.log.warning(
                f"{instrument_code}/{contract_date_str}: force-fetch returned 0 rows"
            )
            return False

        data.log.warning(
            f"{instrument_code}/{contract_date_str}: force-fetch got {len(prices)} rows, "
            f"last={prices.index[-1].date()}"
        )
        return True

    except Exception as e:
        data.log.warning(
            f"{instrument_code}/{contract_date_str}: force-fetch failed: {e}"
        )
        return False


def roll_instrument(data, instrument_code, store, force=False):
    """
    Attempt to roll instrument_code if its PRICE_CONTRACT is stale (or force=True).
    Records outcomes to the RollIssueStore.
    Returns success/failure.
    """
    diag = diagPrices(data)

    try:
        mult = diag.get_multiple_prices(instrument_code)
    except Exception as e:
        data.log.warning(f"Could not get multiple prices for {instrument_code}: {e}")
        return failure

    last_row = mult.iloc[-1]
    price_contract = str(last_row['PRICE_CONTRACT'])
    forward_contract = str(last_row.get('FORWARD_CONTRACT', ''))
    if pd.isna(last_row.get('FORWARD_CONTRACT')):
        forward_contract = ''

    # Staleness check (skip if not stale and not a forced retry).
    #
    # IMPORTANT: this must check the RAW per-contract price feed, not the
    # merged multiple_prices column. multiple_prices is only rebuilt once a
    # day by run_daily_update_multiple_adjusted_prices, which runs AFTER this
    # job in the cron schedule — so its tail is structurally always close to
    # a day behind, even when the underlying contract is trading completely
    # normally. Checking that lagging column caused false "stale" positives
    # (e.g. GBP_micro/AUD_micro force-rolled away from September on 2026-07-01
    # while September was still trading with normal volume through 2026-07-02)
    # that had nothing to do with real contract liquidity or expiry.
    last_real_date = _last_real_price_date(diag, instrument_code, price_contract)
    if last_real_date is None:
        data.log.warning(
            f"{instrument_code}: could not read raw contract prices for {price_contract} "
            f"— falling back to multiple_prices check"
        )

    if last_real_date is None:
        # Fall back to the old (lagging) check only if the raw per-contract
        # feed itself is unavailable.
        real_mask = (mult['PRICE_CONTRACT'].astype(str) == price_contract) & mult['PRICE'].notna()
        if not real_mask.any():
            data.log.warning(f"{instrument_code}: no real PRICE found for {price_contract}, skipping")
            return failure
        last_real_date = mult[real_mask].index[-1].date()

    days_stale = (datetime.date.today() - last_real_date).days

    if not force and days_stale <= STALE_DAYS_THRESHOLD:
        return success  # not stale, nothing to do

    data.log.warning(
        f"{instrument_code}: PRICE_CONTRACT {price_contract} last real price "
        f"{days_stale}d ago ({last_real_date}), FORWARD={forward_contract or 'unknown'} "
        f"— attempting programmatic roll"
    )

    update_positions = updatePositions(data)
    update_positions.set_roll_state(instrument_code, roll_adj_state)

    # First roll attempt
    rolling_obj, err = _attempt_roll(data, instrument_code)

    if rolling_obj is None:
        data.log.warning(
            f"{instrument_code}: initial roll attempt failed ({err}) "
            f"— will try force-fetching FORWARD contract"
        )

        fetched = False
        if forward_contract and forward_contract not in ('nan', 'None', ''):
            fetched = _force_fetch_contract(data, instrument_code, forward_contract)
        else:
            data.log.warning(
                f"{instrument_code}: no FORWARD_CONTRACT in multiple prices, cannot force-fetch"
            )

        if fetched:
            rolling_obj, err = _attempt_roll(data, instrument_code)

        if rolling_obj is None:
            data.log.critical(
                f"{instrument_code}: roll failed after force-fetch attempt. "
                f"Manual intervention required. Last error: {err}"
            )
            update_positions.set_roll_state(instrument_code, default_state)
            store.record_failure(instrument_code, price_contract, forward_contract, err)
            return failure

    # Liquidity gate on the roll TARGET before finalizing.
    #
    # Without this, a roll can mechanically "succeed" (pysystemtrade can compute
    # a valid backadjustment splice) purely from a thin historical overlap, even
    # though the resulting priced contract has no real going-forward liquidity.
    # That contract then immediately looks stale again next run, triggering
    # another roll — a runaway ratchet that marched GOLD through 11 contracts
    # in 5 weeks (Oct'26 -> Jun'28) before landing on a contract IB had no
    # price data for at all. Refusing to finalize into an illiquid target
    # breaks the cascade: the roll fails and surfaces in auto_roll_issues for
    # manual review instead of silently advancing further.
    try:
        new_priced_contract = str(rolling_obj.updated_multiple_prices.iloc[-1]['PRICE_CONTRACT'])
    except Exception:
        new_priced_contract = forward_contract

    new_last_real_date = _last_real_price_date(diag, instrument_code, new_priced_contract)
    new_days_stale = (
        None if new_last_real_date is None
        else (datetime.date.today() - new_last_real_date).days
    )

    if new_last_real_date is None or new_days_stale > STALE_DAYS_THRESHOLD:
        data.log.critical(
            f"{instrument_code}: refusing to finalize roll into {new_priced_contract} — "
            f"target contract has "
            f"{'no' if new_last_real_date is None else f'{new_days_stale}d-stale'} "
            f"real price data. Manual intervention required."
        )
        update_positions.set_roll_state(instrument_code, default_state)
        store.record_failure(
            instrument_code, price_contract, new_priced_contract,
            "roll target has no recent/liquid price data",
        )
        return failure

    rolling_obj.write_new_rolled_data()
    update_positions.set_roll_state(instrument_code, default_state)
    store.record_success(instrument_code)

    data.log.warning(f"{instrument_code}: programmatic roll succeeded → No_Roll")
    return success


def run_auto_roll_expired():
    store = RollIssueStore()

    # Previously-failed instruments are always retried, regardless of staleness
    pending = set(store.pending_instruments())
    if pending:
        print(f"Retrying {len(pending)} previously-failed instruments: {sorted(pending)}")

    with dataBlob(log_name="auto-roll-expired") as data:
        diag = diagPrices(data)
        all_instruments = diag.get_list_of_instruments_in_multiple_prices()

        failed = {}
        for instrument_code in sorted(all_instruments):
            if instrument_code in EXCLUDED_INSTRUMENTS:
                continue
            force_retry = instrument_code in pending
            result = roll_instrument(data, instrument_code, store, force=force_retry)
            if result is failure:
                failed[instrument_code] = "FAILED"

    if failed:
        print("\nInstruments that failed to roll (stored in auto_roll_issues):")
        for inst, status in failed.items():
            print(f"  {inst}: {status}")
    else:
        print("No failed rolls.")

    # Print current issue board
    all_issues = store.all_issues()
    open_issues = [i for i in all_issues if i.get("status") != "resolved"]
    if open_issues:
        print(f"\nOpen roll issues ({len(open_issues)}):")
        for issue in sorted(open_issues, key=lambda x: x.get("instrument_code", "")):
            print(
                f"  {issue['instrument_code']}: status={issue['status']}, "
                f"attempts={issue.get('attempt_count', 0)}, "
                f"first_detected={issue.get('first_detected', '?')}, "
                f"forward={issue.get('forward_contract', '?')}"
            )


if __name__ == "__main__":
    run_auto_roll_expired()
