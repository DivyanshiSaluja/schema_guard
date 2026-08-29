import unittest

from src.common.models import SandboxResult, ValidationReport
from src.validator.data_quality import DataQualityResult
from src.validator.performance import compute_performance_metrics
from src.validator.report import build_validation_report


def sandbox(*, success: bool = True, before: int = 2, after: int = 2):
    return SandboxResult(
        candidate_id="sandbox-id",
        ran_successfully=success,
        row_count_before=before,
        row_count_after=after,
        execution_time_ms=3.25,
    )


def quality(*, passed: bool = True, row_count_ok: bool = True):
    return DataQualityResult(
        candidate_id="quality-id",
        data_quality_ok=passed,
        row_count_ok=row_count_ok,
        required_columns_ok=passed,
        null_values_ok=passed,
        duplicate_ids_ok=passed,
        schema_ok=passed,
    )


class ValidationReportTests(unittest.TestCase):
    def test_all_checks_pass(self):
        report = build_validation_report(
            "candidate-1",
            True,
            quality(),
            sandbox(),
            compute_performance_metrics(sandbox()),
        )

        self.assertIsInstance(report, ValidationReport)
        self.assertTrue(report.schema_ok)
        self.assertTrue(report.data_quality_ok)
        self.assertEqual(report.confidence_score, 1.0)

    def test_schema_failure(self):
        report = build_validation_report("candidate-1", False, quality(), sandbox())

        self.assertFalse(report.schema_ok)
        self.assertTrue(report.data_quality_ok)
        self.assertEqual(report.confidence_score, 0.0)

    def test_sandbox_failure_prevents_successful_data_quality_report(self):
        report = build_validation_report(
            "candidate-1", True, quality(), sandbox(success=False, after=0)
        )

        self.assertFalse(report.data_quality_ok)
        self.assertEqual(report.confidence_score, 0.0)

    def test_data_quality_failure(self):
        report = build_validation_report("candidate-1", True, quality(passed=False), sandbox())

        self.assertFalse(report.data_quality_ok)
        self.assertEqual(report.confidence_score, 0.0)

    def test_row_count_mismatch(self):
        report = build_validation_report(
            "candidate-1", True, quality(passed=False, row_count_ok=False), sandbox(after=1)
        )

        self.assertFalse(report.data_quality_ok)
        self.assertEqual(report.confidence_score, 0.0)

    def test_invalid_data(self):
        invalid_quality = DataQualityResult(
            candidate_id="quality-id",
            data_quality_ok=False,
            row_count_ok=True,
            required_columns_ok=False,
            null_values_ok=False,
            duplicate_ids_ok=True,
            schema_ok=False,
            errors=("missing required columns: email",),
        )

        report = build_validation_report("candidate-1", True, invalid_quality, sandbox())

        self.assertFalse(report.data_quality_ok)
        self.assertEqual(report.confidence_score, 0.0)

    def test_candidate_id_is_propagated_from_argument(self):
        report = build_validation_report("the-requested-id", True, quality(), sandbox())

        self.assertEqual(report.candidate_id, "the-requested-id")

    def test_confidence_score_is_deterministic(self):
        inputs = ("candidate-1", True, quality(), sandbox())

        first = build_validation_report(*inputs)
        second = build_validation_report(*inputs)

        self.assertEqual(first.confidence_score, second.confidence_score)

    def test_validation_report_fields_are_populated(self):
        report = build_validation_report("candidate-1", True, quality(), sandbox())

        self.assertEqual(
            report,
            ValidationReport(
                candidate_id="candidate-1",
                schema_ok=True,
                data_quality_ok=True,
                confidence_score=1.0,
                ranked_position=0,
            ),
        )


if __name__ == "__main__":
    unittest.main()
