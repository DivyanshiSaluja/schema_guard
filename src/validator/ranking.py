"""Deterministic ranking of validation reports."""

from __future__ import annotations

from dataclasses import replace

from src.common.models import ValidationReport


def effective_confidence(report: ValidationReport) -> float:
    """Return a bounded confidence score consistent with report validity.

    Failed schema or data-quality validation always produces zero confidence.
    Otherwise the existing score is retained within the normal ``[0.0, 1.0]``
    confidence range. This is intentionally simple and replaceable later.
    """

    if not (report.schema_ok and report.data_quality_ok):
        return 0.0
    return max(0.0, min(1.0, float(report.confidence_score)))


def rank_candidates(reports: list[ValidationReport]) -> list[ValidationReport]:
    """Return reports ranked by confidence without mutating the input list.

    Ties are resolved by ascending candidate ID. Each returned report is a
    copy with its rationalized confidence and one-based ranked position.
    """

    ordered = sorted(
        reports,
        key=lambda report: (-effective_confidence(report), report.candidate_id),
    )
    return [
        replace(
            report,
            confidence_score=effective_confidence(report),
            ranked_position=position,
        )
        for position, report in enumerate(ordered, start=1)
    ]
