#!/usr/bin/env python3
"""Force IB historical data fetch for ETHER-micro and BITCOIN June/Sep 2026 contracts."""
import sys
sys.path.insert(0, '/pysystemtrade')
sys.path.insert(0, '/pysystemtrade/private')

from sysdata.data_blob import dataBlob
from sysproduction.update_historical_prices import (
    update_historical_prices_for_instrument_and_contract,
    get_config_for_price_filtering,
)
from sysproduction.data.contracts import dataContracts
from sysobjects.contracts import futuresContract

contracts_to_force = [
    ('ETHER-micro', '20260600'),
    ('ETHER-micro', '20260900'),
    ('BITCOIN', '20260600'),
    ('BITCOIN', '20260900'),
]

with dataBlob(log_name='force-crypto') as data:
    dc = dataContracts(data)
    cleaning_config = get_config_for_price_filtering(data)

    for inst, cdate in contracts_to_force:
        c = futuresContract(inst, cdate)
        print(f'\nForcing price update for {inst}/{cdate}...')

        # Ensure contract is in MongoDB and marked as sampling
        if not dc.is_contract_in_data(c):
            print(f'  Adding {inst}/{cdate} to MongoDB...')
            dc.add_contract_data(c, ignore_duplication=True)
        dc.mark_contract_as_sampling(c)
        print(f'  Marked as sampling')

        try:
            update_historical_prices_for_instrument_and_contract(c, data, cleaning_config=cleaning_config)
            print(f'  Done')
        except Exception as e:
            import traceback
            print(f'  ERROR: {e}')
            traceback.print_exc()
