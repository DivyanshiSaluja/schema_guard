import subprocess, json, os
from dataclasses import dataclass

NETWORK_NAME = "schema_guard_default"   # replace with your exact network name from `docker network ls`

@dataclass
class SandboxResult:
    passed: bool
    log: str

def run_in_sandbox(candidate_id: str, code: str) -> SandboxResult:
    result = subprocess.run(
        ["docker", "run", "--rm",
         "--network", NETWORK_NAME,
         "-v", f"{os.getcwd()}\\data\\candidates:/candidates",
         "schemaguard-sandbox", "python", "sandbox_entry.py",
         f"/candidates/{candidate_id}.py"],
        capture_output=True, text=True, timeout=30,
    )

    combined_output = (result.stdout + result.stderr).strip()
    print(f"[sandbox raw output] {combined_output!r}")   # always visible now, for debugging

    if not combined_output:
        return SandboxResult(passed=False, log="Sandbox produced no output at all (check network/container).")

    last_line = combined_output.strip().splitlines()[-1]
    try:
        parsed = json.loads(last_line)
        passed = parsed.get("status") == "SUCCESS"
        log = json.dumps(parsed)
    except json.JSONDecodeError:
        passed = False
        log = f"Could not parse output as JSON: {combined_output}"

    return SandboxResult(passed=passed, log=log)