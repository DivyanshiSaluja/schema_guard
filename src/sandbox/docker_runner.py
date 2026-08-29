import subprocess, json
from dataclasses import dataclass

@dataclass
class SandboxResult:
    passed: bool
    log: str

def run_in_sandbox(candidate_id: str, code: str) -> SandboxResult:
    path = f"data/candidates/{candidate_id}.py"
    result = subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{__import__('os').getcwd()}\\data\\candidates:/candidates",
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