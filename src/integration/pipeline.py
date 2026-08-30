"""Minimal orchestration between Person A input and Person B components."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

from src.common.models import RepairCandidate, SandboxResult, ValidationReport
from src.deploy.deployer import DeploymentResult, deploy_approved_candidate
from src.deploy.rollback import RollbackResult, rollback_latest
from src.review.review_api import (
    ApprovalError,
    ReviewState,
    approve_candidate,
    build_review_items,
    get_review_state,
    reject_candidate,
)
from src.validator.data_quality import DataQualityResult, validate_data_quality
from src.validator.performance import PerformanceMetrics, compute_performance_metrics
from src.validator.ranking import rank_candidates
from src.validator.report import build_validation_report


@dataclass(frozen=True)
class CandidateInput:
    candidate: RepairCandidate
    sandbox_result: SandboxResult
    schema_ok: bool
    output_rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class IntegrationResult:
    reports: tuple[ValidationReport, ...] = ()
    review_state: ReviewState | None = None
    deployment: DeploymentResult | None = None
    rollback: RollbackResult | None = None
    error: str = ""


def load_candidates_json(path: str | Path) -> tuple[CandidateInput, ...]:
    """Load the small integration contract used by the MVP.

    Each record contains either a top-level candidate or a nested ``candidate``
    object, a ``sandbox_result`` object, ``schema_ok``, and ``output_rows``.
    """

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not load candidates JSON: {error}") from error

    if isinstance(raw, dict):
        raw = raw.get("candidates")
    if not isinstance(raw, list):
        raise ValueError("candidates JSON must contain a list or a 'candidates' list")

    try:
        return tuple(_deserialize_candidate(record) for record in raw)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid candidate record: {error}") from error


def run_integration(
    candidates_path: str | Path,
    *,
    approved_candidate_id: str | None = None,
    rejected_candidate_ids: tuple[str, ...] = (),
    etl_runner: Callable[[], object] | None = None,
    source_path: str | Path | None = None,
    snapshot_dir: str | Path | None = None,
) -> IntegrationResult:
    """Run validation, review, and optional deployment for candidate input."""

    try:
        inputs = load_candidates_json(candidates_path)
        if not inputs:
            return IntegrationResult(error="no candidates found")

        candidates = [item.candidate for item in inputs]
        sandbox_results = [item.sandbox_result for item in inputs]
        performance = [compute_performance_metrics(result) for result in sandbox_results]
        data_quality: list[DataQualityResult] = [
            validate_data_quality(item.output_rows, item.sandbox_result) for item in inputs
        ]
        reports = [
            build_validation_report(
                item.candidate.id,
                item.schema_ok,
                quality,
                item.sandbox_result,
                metrics,
            )
            for item, quality, metrics in zip(inputs, data_quality, performance)
        ]
        ranked_reports = tuple(rank_candidates(reports))
        review_items = build_review_items(
            candidates,
            ranked_reports,
            sandbox_results,
            performance,
        )
        review_state = get_review_state(review_items)
        for candidate_id in rejected_candidate_ids:
            review_state = reject_candidate(review_state, candidate_id)

        if approved_candidate_id is None:
            return IntegrationResult(
                reports=ranked_reports,
                review_state=review_state,
            )

        review_state = approve_candidate(review_state, approved_candidate_id)
        candidate = next(
            item.candidate for item in inputs if item.candidate.id == approved_candidate_id
        )
        deployment = deploy_approved_candidate(
            candidate,
            review_state,
            etl_runner=etl_runner,
            **_optional_paths(source_path, snapshot_dir),
        )
        rollback = None
        if not deployment.deployed and deployment.snapshot_path is not None:
            rollback = rollback_latest(
                etl_runner=etl_runner,
                **_optional_paths(source_path, snapshot_dir),
            )
        return IntegrationResult(
            reports=ranked_reports,
            review_state=review_state,
            deployment=deployment,
            rollback=rollback,
        )
    except (ApprovalError, KeyError, ValueError) as error:
        return IntegrationResult(error=str(error))


def _deserialize_candidate(record: object) -> CandidateInput:
    if not isinstance(record, dict):
        raise TypeError("record must be an object")
    candidate_data = record.get("candidate", record)
    sandbox_data = record.get("sandbox_result", record.get("sandbox"))
    if not isinstance(candidate_data, dict) or not isinstance(sandbox_data, dict):
        raise ValueError("candidate and sandbox_result objects are required")

    candidate_id = str(candidate_data["id"])
    candidate = RepairCandidate(
        id=candidate_id,
        code=str(candidate_data["code"]),
        explanation=str(candidate_data.get("explanation", "")),
        passed_sandbox=bool(candidate_data.get("passed_sandbox", False)),
        attempt=int(candidate_data.get("attempt", 1)),
        sandbox_log=str(candidate_data.get("sandbox_log", "")),
    )
    sandbox = SandboxResult(
        candidate_id=str(sandbox_data.get("candidate_id", candidate_id)),
        ran_successfully=bool(sandbox_data["ran_successfully"]),
        row_count_before=int(sandbox_data["row_count_before"]),
        row_count_after=int(sandbox_data["row_count_after"]),
        execution_time_ms=float(sandbox_data["execution_time_ms"]),
        error_log=str(sandbox_data.get("error_log", "")),
    )
    if sandbox.candidate_id != candidate_id:
        raise ValueError("candidate and sandbox candidate IDs must match")
    output_rows = record.get("output_rows", [])
    if not isinstance(output_rows, list) or not all(
        isinstance(row, dict) for row in output_rows
    ):
        raise ValueError("output_rows must be a list of objects")
    return CandidateInput(
        candidate=candidate,
        sandbox_result=sandbox,
        schema_ok=bool(record.get("schema_ok", False)),
        output_rows=tuple(output_rows),
    )


def _optional_paths(source_path, snapshot_dir) -> dict[str, str | Path]:
    paths = {}
    if source_path is not None:
        paths["source_path"] = source_path
    if snapshot_dir is not None:
        paths["snapshot_dir"] = snapshot_dir
    return paths
