import pexpect
import sys
import time

script_path = "/pysystemtrade/sysproduction/linux/scripts/interactive_controls"
source_cmd  = f". {script_path}"

child = pexpect.spawn("bash", encoding="utf-8", timeout=240)
child.logfile = sys.stdout

# 1) Source the script
child.sendline(source_cmd)
child.expect(r"Your choice\? <RETURN for EXIT>", timeout=120)
child.sendline("4")
child.expect(r"Your choice\? <RETURN for Back>", timeout=120)
child.sendline("44")
child.expect(r"Your choice\? <RETURN for Back>", timeout=120)
child.sendline("")   # back out of submenu
child.expect(r"Your choice\? <RETURN for EXIT>", timeout=120)
child.sendline("")   # exit interactive_controls
child.expect("FINISHED", timeout=120)

# ← NEW: shut down the bash shell
child.sendline("exit")      # tell bash to exit
child.expect(pexpect.EOF)    # now we get EOF
child.close()
print("Automation complete.")
