"""Deployment of a candidate approved by the review layer."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.common.models import RepairCandidate
from src.deploy.snapshot_manager import (
    DEFAULT_SOURCE_PATH,
    DEFAULT_SNAPSHOT_DIR,
    SnapshotManager,
)
from src.review.review_api import ReviewState, ReviewStatus


@dataclass(frozen=True)
class DeploymentResult:
    candidate_id: str
    deployed: bool
    snapshot_path: Path | None
    etl_succeeded: bool
    error_log: str = ""


def deploy_approved_candidate(
    candidate: RepairCandidate,
    review_state: ReviewState,
    *,
    etl_runner: Callable[[], object] | None = None,
    source_path: Path | str = DEFAULT_SOURCE_PATH,
    snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
) -> DeploymentResult:
    """Snapshot, write, and execute an explicitly approved candidate.

    The deployer enforces the review decision but deliberately does not repeat
    schema, sandbox, or data-quality validation.
    """

    approved_item = next(
        (item for item in review_state.items if item.candidate_id == candidate.id),
        None,
    )
    if (
        review_state.approved_candidate_id != candidate.id
        or approved_item is None
        or approved_item.status != ReviewStatus.APPROVED
    ):
        return DeploymentResult(
            candidate_id=candidate.id,
            deployed=False,
            snapshot_path=None,
            etl_succeeded=False,
            error_log="candidate is not approved for deployment",
        )

    manager = SnapshotManager(source_path, snapshot_dir)
    snapshot_path = manager.snapshot_current_pipeline()
    if snapshot_path is None:
        return DeploymentResult(
            candidate_id=candidate.id,
            deployed=False,
            snapshot_path=None,
            etl_succeeded=False,
            error_log="could not snapshot current pipeline",
        )

    try:
        Path(source_path).write_text(candidate.code, encoding="utf-8")
        etl_succeeded, error_log = run_etl(etl_runner)
        return DeploymentResult(
            candidate_id=candidate.id,
            deployed=etl_succeeded,
            snapshot_path=snapshot_path,
            etl_succeeded=etl_succeeded,
            error_log=error_log,
        )
    except (OSError, UnicodeError) as error:
        return DeploymentResult(
            candidate_id=candidate.id,
            deployed=False,
            snapshot_path=snapshot_path,
            etl_succeeded=False,
            error_log=str(error),
        )


def run_etl(etl_runner: Callable[[], object] | None = None) -> tuple[bool, str]:
    """Run the existing ETL, or an injected test runner."""

    try:
        if etl_runner is not None:
            outcome = etl_runner()
            if isinstance(outcome, bool) and not outcome:
                return False, "ETL runner reported failure"
            return True, ""
        completed = subprocess.run(
            [sys.executable, "-m", "src.pipeline.etl_pipeline"],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return False, completed.stderr or completed.stdout
        return True, completed.stdout
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
