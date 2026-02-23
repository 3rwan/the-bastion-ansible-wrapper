# Error Handling Refactoring Plan

## Problem Analysis

In your scenario, Ansible shows a highly generic `UNREACHABLE` error when a connection via the script fails (e.g. your public key is refused by The Bastion). The actual SSH error message (`Permission denied (publickey)`) is completely lost.

This occurs due to two major architectural choices in the wrapper scripts (`sshwrapper.py` and `scpwrapper.py`):
1. **Use of `os.execv()` Process Replacement**: The wrapper scripts construct the SSH arguments and use `os.execv()` to unconditionally replace the current Python process with the `ssh` binary. Because Python relinquishes control entirely, it cannot inspect if `ssh` succeeded or failed, nor can it intercept its Output/Error message streams before passing them back to Ansible.
2. **Aggressive usage of the Quiet Flag (`-q`)**: In `sshwrapper.py`, the `-q` argument is hardcoded unconditionally for the initial SSH connection to the bastion (`args = ["ssh", "-p", bastion_port, "-q", ...]`). This suppresses SSH warnings and errors, which are helpful for diagnosis.

## Proposed Strategy & Reflections

To provide explicit, user-friendly errors when a Bastion connection fails, the wrapper scripts must retain control of the process execution.

### Strategy 1: Replace `os.execv` with `subprocess.run`
Instead of replacing the Python process with `ssh`, we should spawn `ssh` as a child process using the `subprocess` module. This allows us to intercept the execution, capture `stderr`, and inspect the return code. If the process fails, we can analyze the output to inject our own custom error context into `sys.stderr` before Ansible intercepts and fails the task.

### Strategy 2: Remove the `-q` flag
Removing the `-q` flag will allow native SSH warnings and authentication errors to bubble up to `stderr`, making it possible for our `subprocess` to actually read and identify what went wrong (e.g. "Permission denied").

### Strategy 3: Parse `stderr` for Known Bastion Errors
When `subprocess.run` returns a non-zero exit code, evaluate `stderr`. If it contains `"Permission denied (publickey)"`, output a very explicit, brightly formatted error to `sys.stderr` indicating that the failure occurred **at the Bastion level**, not the target host level.

---

## Step-by-Step Directions (To do)

- [ ] **Step 1:** In `sshwrapper.py`, replace `os.execv(...)` with `subprocess.run(...)`.
  - Import `subprocess` and `sys`.
  - Example logic:
  ```python
  import subprocess
  import sys
  
  # ... Construct your arguments ...
  
  result = subprocess.run(
      args,
      stdout=sys.stdout,         # Pass stdout directly to Ansible
      stderr=subprocess.PIPE,    # Capture stderr for analysis
      text=True
  )
  ```
- [ ] **Step 2:** Add error handling after the `subprocess.run()` call.
  - Check `result.returncode`. If it's not `0`, the command failed.
  - Check if `"Permission denied"` is present in `result.stderr`.
  - If handled, write a custom message to `sys.stderr` explaining exactly what went wrong with the Bastion connection.
  - Always write the original `result.stderr` out to `sys.stderr` so Ansible still has it, and exit with `sys.exit(result.returncode)`.
- [ ] **Step 3:** Drop the hardcoded `"-q"` argument from the initial argument list in `sshwrapper.py` to ensure SSH errors are fully emitted to `stderr`.
- [ ] **Step 4:** Replicate the exact same `subprocess` intercept logic in `scpwrapper.py`.
- [ ] **Step 5:** Replicate the exact same `subprocess` intercept logic in `sftpwrapper.py`.
- [ ] **Step 6:** Trigger a connection with a bad SSH key to verify Ansible prints the custom error.
