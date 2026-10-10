import json
import os
import subprocess
from dataclasses import dataclass

@dataclass
class SandboxResult:
    passed: bool
    log: str

def run_in_sandbox(candidate_id: str, code: str) -> SandboxResult:
    candidates_dir = os.path.join(os.getcwd(), "data", "candidates")
    result = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--read-only",
         "--memory", "256m", "--cpus", "1",
         "-v", f"{candidates_dir}:/candidates:ro",
         "schemaguard-sandbox", "python", "sandbox_entry.py",
         f"/candidates/{candidate_id}.py"],
        capture_output=True, text=True, timeout=30,
    )
    output = result.stdout.strip().splitlines()
    last_line = output[-1] if output else result.stderr
    try:
        parsed = json.loads(last_line)
        passed = parsed.get("status") == "SUCCESS"
    except Exception:
        passed = False
    return SandboxResult(passed=passed, log=last_line or result.stderr)
