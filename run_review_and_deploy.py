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

# 2. Measure each candidate on the live source rows, in their current
# (post-drift) shape, instead of deriving results from passed_sandbox.
from sqlalchemy import create_engine, text
from src.common.config import DB_URL
from src.sandbox.executor import execute_candidate, _compile_transform
from src.validator.data_quality import validate_data_quality
from src.validator.ranking import rank_candidates
from src.validator.report import build_validation_report

with create_engine(DB_URL).connect() as conn:
    live_rows = [dict(r) for r in conn.execute(text("SELECT * FROM public.customers")).mappings()]

sandbox_results, reports = [], []
for c in candidates:
    result = execute_candidate(c, live_rows)
    output_rows = []
    if result.ran_successfully:
        transform = _compile_transform(c.code)
        output_rows = [transform(dict(row)) for row in live_rows]
    quality = validate_data_quality(output_rows, result)
    sandbox_results.append(result)
    reports.append(build_validation_report(c.id, quality.schema_ok, quality, result))
reports = rank_candidates(reports)

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