#!/usr/bin/env python3
"""Force IB historical data fetch for COPPER-micro July/Aug 2026 contracts."""
import sys
sys.path.insert(0, '/pysystemtrade')
sys.path.insert(0, '/pysystemtrade/private')

from sysdata.data_blob import dataBlob
from sysproduction.update_historical_prices import (
    update_historical_prices_for_instrument,
    update_historical_prices_for_instrument_and_contract,
    get_config_for_price_filtering,
)
from sysobjects.contracts import futuresContract

with dataBlob(log_name='force-copper') as data:
    cleaning_config = get_config_for_price_filtering(data)
    print('Updating COPPER-micro using sampled contracts list...')
    result = update_historical_prices_for_instrument('COPPER-micro', data, cleaning_config=cleaning_config)
    print(f'Result: {result}')

    for contract_date in ['20260700', '20260800']:
        c = futuresContract('COPPER-micro', contract_date)
        print(f'\nForcing contract-level update for COPPER-micro/{contract_date}...')
        try:
            update_historical_prices_for_instrument_and_contract(c, data, cleaning_config=cleaning_config)
            print(f'  Done')
        except Exception as e:
            import traceback
            print(f'  ERROR: {e}')
            traceback.print_exc()
