"""Pure in-memory API for human review decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

from src.common.models import RepairCandidate, SandboxResult, ValidationReport
from src.validator.performance import PerformanceMetrics


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalError(ValueError):
    """Raised when a candidate cannot be approved."""


@dataclass(frozen=True)
class ReviewItem:
    """Candidate presentation data plus its already-computed validations."""

    candidate: RepairCandidate
    report: ValidationReport
    sandbox_result: SandboxResult | None = None
    performance_metrics: PerformanceMetrics | None = None
    status: ReviewStatus = ReviewStatus.PENDING

    @property
    def candidate_id(self) -> str:
        return self.candidate.id


@dataclass(frozen=True)
class ReviewState:
    """Immutable in-memory review decision state."""

    items: tuple[ReviewItem, ...]
    selected_candidate_id: str | None = None
    approved_candidate_id: str | None = None


def build_review_items(
    candidates: Iterable[RepairCandidate],
    reports: Iterable[ValidationReport],
    sandbox_results: Iterable[SandboxResult] = (),
    performance_metrics: Iterable[PerformanceMetrics] = (),
) -> tuple[ReviewItem, ...]:
    """Join presentation data with existing validation results in memory.

    Reports are displayed in ranked order when ``ranked_position`` is set;
    otherwise candidate ID supplies a deterministic order. No input object is
    modified.
    """

    candidate_by_id = _index_unique(candidates, lambda candidate: candidate.id)
    report_by_id = _index_unique(reports, lambda report: report.candidate_id)
    sandbox_by_id = _index_unique(sandbox_results, lambda result: result.candidate_id)
    performance_by_id = _index_unique(
        performance_metrics, lambda metrics: metrics.candidate_id
    )

    missing_candidates = sorted(set(report_by_id) - set(candidate_by_id))
    if missing_candidates:
        raise ValueError("reports without candidates: " + ", ".join(missing_candidates))

    missing_reports = sorted(set(candidate_by_id) - set(report_by_id))
    if missing_reports:
        raise ValueError("candidates without reports: " + ", ".join(missing_reports))

    items = tuple(
        ReviewItem(
            candidate=candidate_by_id[candidate_id],
            report=report_by_id[candidate_id],
            sandbox_result=sandbox_by_id.get(candidate_id),
            performance_metrics=performance_by_id.get(candidate_id),
        )
        for candidate_id in report_by_id
    )
    return tuple(
        sorted(
            items,
            key=lambda item: (
                item.report.ranked_position
                if item.report.ranked_position > 0
                else float("inf"),
                item.candidate_id,
            ),
        )
    )


def get_review_state(items: Iterable[ReviewItem]) -> ReviewState:
    """Create a pending review state from review items."""

    materialized = tuple(items)
    _index_unique(materialized, lambda item: item.candidate_id)
    return ReviewState(items=materialized)


def select_candidate(state: ReviewState, candidate_id: str) -> ReviewState:
    """Select a non-rejected candidate for review."""

    item = _find_item(state, candidate_id)
    if item.status == ReviewStatus.REJECTED:
        raise ValueError(f"rejected candidate cannot be selected: {candidate_id}")
    return replace(state, selected_candidate_id=candidate_id)


def reject_candidate(state: ReviewState, candidate_id: str) -> ReviewState:
    """Mark a candidate rejected and clear approval if it was approved."""

    _find_item(state, candidate_id)
    items = _replace_status(state.items, candidate_id, ReviewStatus.REJECTED)
    return replace(
        state,
        items=items,
        selected_candidate_id=(
            None if state.selected_candidate_id == candidate_id else state.selected_candidate_id
        ),
        approved_candidate_id=(
            None if state.approved_candidate_id == candidate_id else state.approved_candidate_id
        ),
    )


def approve_candidate(state: ReviewState, candidate_id: str) -> ReviewState:
    """Approve a candidate only when all required validations succeeded."""

    item = _find_item(state, candidate_id)
    if not item.report.schema_ok:
        raise ApprovalError("candidate failed schema validation")
    if not item.report.data_quality_ok:
        raise ApprovalError("candidate failed data-quality validation")
    if item.sandbox_result is None or not item.sandbox_result.ran_successfully:
        raise ApprovalError("candidate failed sandbox execution")

    items = tuple(
        replace(
            current,
            status=(
                ReviewStatus.APPROVED
                if current.candidate_id == candidate_id
                else (
                    ReviewStatus.PENDING
                    if current.status == ReviewStatus.APPROVED
                    else current.status
                )
            ),
        )
        for current in state.items
    )
    return replace(
        state,
        items=items,
        selected_candidate_id=candidate_id,
        approved_candidate_id=candidate_id,
    )


def can_approve(item: ReviewItem) -> bool:
    """Return whether the dashboard should enable approval for an item."""

    return bool(
        item.report.schema_ok
        and item.report.data_quality_ok
        and item.sandbox_result is not None
        and item.sandbox_result.ran_successfully
    )


def _index_unique(values: Iterable, key) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for value in values:
        value_key = key(value)
        if value_key in indexed:
            raise ValueError(f"duplicate candidate ID: {value_key}")
        indexed[value_key] = value
    return indexed


def _find_item(state: ReviewState, candidate_id: str) -> ReviewItem:
    for item in state.items:
        if item.candidate_id == candidate_id:
            return item
    raise KeyError(f"unknown candidate ID: {candidate_id}")


def _replace_status(
    items: tuple[ReviewItem, ...], candidate_id: str, status: ReviewStatus
) -> tuple[ReviewItem, ...]:
    return tuple(
        replace(item, status=status) if item.candidate_id == candidate_id else item
        for item in items
    )
