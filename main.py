"""Command-line entry point for the integrated SchemaGuard MVP."""

from __future__ import annotations

import argparse

from src.integration.pipeline import run_integration


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the SchemaGuard MVP flow")
    parser.add_argument("--candidates", required=True, help="Path to candidates.json")
    parser.add_argument(
        "--approve",
        dest="approved_candidate_id",
        help="Candidate ID approved for deployment",
    )
    args = parser.parse_args()
    result = run_integration(
        args.candidates,
        approved_candidate_id=args.approved_candidate_id,
    )
    if result.error:
        print(f"Integration failed: {result.error}")
        return 1
    print(f"Ranked candidates: {[report.candidate_id for report in result.reports]}")
    if result.deployment is None:
        print("Review complete; no candidate approved for deployment.")
    elif result.deployment.deployed:
        print(f"Deployed candidate: {result.deployment.candidate_id}")
    else:
        print(f"Deployment failed: {result.deployment.error_log}")
        if result.rollback is not None:
            print(f"Rollback succeeded: {result.rollback.rollback_succeeded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
