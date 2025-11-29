#!/usr/bin/env python3
"""
Automated price checker.
Uses pexpect to automate the interactive_manual_check_historical_prices script.
"""
import pexpect
import sys
import time

# Full list of instrument codes
instrument_codes = [
    "AEX", "ALUMINIUM", "AUD", "AUDJPY", "AUD_micro", "BBCOMM", "BITCOIN", "BOBL",
    "BONO", "BRE", "BRENT-LAST", "BTP", "BUND", "BUTTER", "BUXL", "CAC", "CAD",
    "CAD_micro", "CHEESE", "CHF", "CHF_micro", "CNH", "COPPER", "COPPER-micro",
    "CORN", "CORN_mini", "CRUDE_W", "CRUDE_W_micro", "DAX", "DOW", "DOW_mini",
    "EDOLLAR", "ETHER-micro", "ETHEREUM", "EUR", "EURCHF", "EUROSTX",
    "EUROSTX-LARGE", "EUR_micro", "FED", "FEEDCOW", "FTSECHINAA", "FTSECHINAH",
    "FTSETAIWAN", "GAS-LAST", "GAS-PEN", "GASOILINE", "GAS_US", "GAS_US_mini",
    "GBP", "GBPCHF", "GBPJPY", "GBP_micro", "GICS", "GOLD", "GOLD_micro",
    "HEATOIL", "INR", "IRON", "JGB-SGX-mini", "JPY", "JPY_mini", "LEANHOG",
    "LIVECOW", "LUMBER-new", "MILK", "MILKDRY", "MILKWET", "MSCISING", "MXP",
    "NASDAQ", "NASDAQ_micro", "NIKKEI", "NZD", "OAT", "OATIES", "OMX", "PALLAD",
    "PLAT", "REDWHEAT", "RUBBER", "RUSSELL", "SHATZ", "SILVER", "SMI", "SOFR",
    "SOFR1", "SOYBEAN", "SOYBEAN_mini", "SOYMEAL", "SOYOIL", "SP500",
    "SP500_micro", "STEEL", "US10", "US10U", "US10Y_micro", "US2", "US20-new",
    "US2Y_micro", "US3", "US30", "US5", "V2X", "VIX", "VIX_mini", "WHEAT",
    "WHEAT_mini"
]

script_path = "/pysystemtrade/sysproduction/linux/scripts/interactive_manual_check_historical_prices"
source_command = f". {script_path}"

# Patterns
price_spike_prompt = r"<return> to accept"
instrument_prompt   = r"Instrument code\?\(Return to EXIT\)"

# Launch bash and source the script
child = pexpect.spawn("bash", encoding="utf-8", timeout=240)
child.logfile = sys.stdout
child.sendline(source_command)
print(f"Sent: {source_command}")

# Wait for the tool to start
child.expect("Do a daily update for futures contract prices", timeout=120)
print("Tool started.")

# Answer the initial "Make changes?" prompt
child.expect("Make changes\\?", timeout=30)
child.sendline("n")
print("Replied 'n' to Make changes?")

# Process each instrument
skip_prompt = False
for code in instrument_codes:
    # Wait until we're asked for an instrument code
    if not skip_prompt:
      child.expect(instrument_prompt, timeout=240)
    print(f"\n>>> Sending instrument code: {code}")
    child.sendline(code)
    skip_prompt = False

    # Now keep accepting spikes until we see the instrument prompt again
    while True:
        idx = child.expect([price_spike_prompt, instrument_prompt], timeout=120)
        if idx == 0:
            # Got a spike confirmation prompt
            print(f"    Spike prompt for {code}; pressing <return> to accept.")
            child.sendline("")   # send <CR>
            time.sleep(0.5)      # small pause before next prompt
        else:
            # Instrument prompt reappeared ⇒ done with this code
            print(f"    Done with {code}.")
            skip_prompt = True
            break

# Finally, exit by pressing <return> at the instrument prompt
child.expect(instrument_prompt, timeout=120)
print("All codes done. Exiting.")
child.sendline("")
# 6) Finish up
child.sendline("exit")
child.expect(pexpect.EOF)
child.close()
print("Automation complete.")
