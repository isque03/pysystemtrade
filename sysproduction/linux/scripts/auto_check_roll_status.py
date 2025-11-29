#!/usr/bin/env python3
"""
Automated roll status checker.
Uses pexpect to automate the interactive_update_roll_status script.
"""
import pexpect
import sys
import time
import re

# Path to your interactive_update_roll_status script
script_path = "/pysystemtrade/sysproduction/linux/scripts/interactive_update_roll_status"

# Combined command: source the script then exit the bash session so EOF is emitted
bash_command = f". {script_path}; exit"

# Spawn bash with -c to run the script and terminate automatically
child = pexpect.spawn("bash", ["-c", bash_command], encoding="utf-8", timeout=60)
child.logfile = sys.stdout

# Patterns for the various prompts we expect
mode_prompt = r"Your choice\?.*<RETURN for Manually input instrument codes.*>"
days_ahead_prompt = r"How many days ahead should I look for expiries\? <RETURN for default.*>"
params_prompt = r"Use default parameters\?.*"
continue_prompt = r"Press return to continue"
menu_prompt = re.compile(r"Have to input roll state[\s\S]*?Your choice\?.*", re.IGNORECASE)
bare_prompt = re.compile(r"Your choice\? <RETURN for No_Roll>", re.IGNORECASE)
confirm_change_prompt = re.compile(r"are you sure y/n.*<RETURN>", re.IGNORECASE)
forward_fill_prompt = re.compile(r"Do you want to try forward filling prices first.*\[y/n\]", re.IGNORECASE)

try:
    # 1) Mode selection
    child.expect(mode_prompt, timeout=180)
    child.sendline("3")
    print("→ Selected mode 3 (fully automated rolls)")

    # 2) Accept default days ahead
    child.expect(days_ahead_prompt, timeout=180)
    child.sendline("")
    print("→ Accepted default look-ahead days")

    # 3) Accept default roll parameters
    child.expect(params_prompt, timeout=180)
    child.sendline("Y")
    print("→ Accepted default roll parameters")

    # 4) Continue past the rules dump
    child.expect(continue_prompt, timeout=180)
    child.sendline("")
    print("→ Continued into roll loop")

    # 5) Process prompts until EOF
    force_choice = None
    while True:
        idx = child.expect([menu_prompt, bare_prompt, confirm_change_prompt,
                             forward_fill_prompt, pexpect.EOF], timeout=180)
        if idx == 0:
            # Full menu: parse for "Force"
            block = child.before + child.after
            print("→ Roll-state menu:\n" + block)
            force_choice = None
            for line in block.splitlines():
                m = re.match(r"\s*(\d+)\s*:\s*(\w+)", line)
                if m and m.group(2).lower() == 'force':
                    force_choice = m.group(1)
                    break
            if force_choice:
                print(f"→ Parsed menu; sending '{force_choice}' for Force")
                child.sendline(force_choice)
            else:
                print("→ 'Force' not found; sending <RETURN>")
                child.sendline("")

        elif idx == 1:
            # Bare prompt: resend last known force_choice
            if force_choice:
                print(f"→ Bare prompt; re-sending '{force_choice}'")
                child.sendline(force_choice)
            else:
                print("→ Bare prompt but no force_choice; sending <RETURN>")
                child.sendline("")

        elif idx == 2:
            # Confirmation prompt
            print("→ Confirmation prompt: sending 'y'")
            child.sendline("y")

        elif idx == 3:
            # Forward-fill prompt
            print("→ Forward-fill prompt: sending 'y'")
            child.sendline("y")

        else:
            # EOF: script and bash shell have terminated
            print("→ Automation script completed.")
            break

        time.sleep(0.2)

finally:
    # Ensure the pexpect child is closed
    if child.isalive():
        child.close(force=True)

print("Automation finished.")
