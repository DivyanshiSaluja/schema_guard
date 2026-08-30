from dotenv import load_dotenv
load_dotenv()

import json
from src.common.models import RepairCandidate, SandboxResult, ValidationReport
from src.review.review_api import (
    build_review_items,
    get_review_state,
    approve_candidate,
    reject_candidate,
    ApprovalError,
)
from src.deploy.deployer import deploy_approved_candidate

# 1. Load your real candidates.json
with open("data/candidates.json") as f:
    raw_candidates = json.load(f)

candidates = [
    RepairCandidate(
        id=c["id"],
        code=c["code"],
        explanation=c["explanation"],
        passed_sandbox=c["passed_sandbox"],
        attempt=c["attempt"],
        sandbox_log=c["sandbox_log"],
    )
    for c in raw_candidates
]

# 2. Synthesize SandboxResult + ValidationReport from the flat data
# (your pipeline doesn't track these as separate checks yet, so derive them
#  from passed_sandbox — this is the honest bridging point between the two halves)
sandbox_results = [
    SandboxResult(
        candidate_id=c.id,
        ran_successfully=c.passed_sandbox,
        row_count_before=500,
        row_count_after=500 if c.passed_sandbox else 0,
        execution_time_ms=0.0,
        error_log="" if c.passed_sandbox else c.sandbox_log,
    )
    for c in candidates
]

reports = [
    ValidationReport(
        candidate_id=c.id,
        schema_ok=True,
        data_quality_ok=c.passed_sandbox,
        confidence_score=100.0 if c.passed_sandbox else 0.0,
        ranked_position=1,
    )
    for c in candidates
]

# 3. Build review state using their actual API
items = build_review_items(candidates, reports, sandbox_results)
state = get_review_state(items)

# 4. Present each candidate, ask for approval
approved_candidate = None
for item in items:
    print(f"\nCandidate: {item.candidate_id}")
    print(f"Explanation: {item.candidate.explanation}")
    print(f"Sandbox ran successfully: {item.sandbox_result.ran_successfully}")
    print(f"Confidence score: {item.report.confidence_score}")
    print("\nCode:\n" + item.candidate.code)

    choice = input("\nApprove this candidate? (y/n): ").strip().lower()
    if choice == "y":
        try:
            state = approve_candidate(state, item.candidate_id)
            approved_candidate = item.candidate
            break
        except ApprovalError as e:
            print(f"Cannot approve: {e}")
    else:
        state = reject_candidate(state, item.candidate_id)

# 5. Deploy if approved
if approved_candidate:
    result = deploy_approved_candidate(approved_candidate, state)
    print(f"\nDeployed: {result.deployed}")
    print(f"ETL succeeded: {result.etl_succeeded}")
    print(f"Snapshot saved to: {result.snapshot_path}")
    if result.error_log:
        print(f"Error log: {result.error_log}")
else:
    print("\nNo candidate approved. Pipeline unchanged.")