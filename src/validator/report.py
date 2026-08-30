"""Integration of Phase 1–3 validation results into a ValidationReport."""

from __future__ import annotations

from src.common.models import SandboxResult, ValidationReport
from src.validator.data_quality import DataQualityResult
from src.validator.performance import PerformanceMetrics


def build_validation_report(
    candidate_id: str,
    schema_ok: bool,
    data_quality_result: DataQualityResult,
    sandbox_result: SandboxResult,
    performance_metrics: PerformanceMetrics | None = None,
) -> ValidationReport:
    """Combine existing validation results into the project's report model.

    Confidence is intentionally a replaceable Phase 4 placeholder: it is
    ``1.0`` only when schema validation, data-quality validation, and sandbox
    execution all succeed; otherwise it is ``0.0``. Performance metrics are
    accepted for pipeline integration, but do not affect confidence because
    no performance threshold has been defined yet.
    """

    # Keep the optional argument part of the integration API without copying
    # or recalculating its values. The report model has no performance fields.
    _ = performance_metrics
    effective_data_quality_ok = (
        data_quality_result.data_quality_ok and sandbox_result.ran_successfully
    )
    all_checks_passed = bool(schema_ok and effective_data_quality_ok)

    return ValidationReport(
        candidate_id=candidate_id,
        schema_ok=bool(schema_ok),
        data_quality_ok=effective_data_quality_ok,
        confidence_score=1.0 if all_checks_passed else 0.0,
    )
