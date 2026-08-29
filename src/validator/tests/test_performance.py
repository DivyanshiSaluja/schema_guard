import unittest

from src.common.models import SandboxResult
from src.validator.performance import compute_performance_metrics


def make_result(
    *,
    success: bool = True,
    before: int = 500,
    after: int = 500,
    elapsed_ms: float = 12.5,
) -> SandboxResult:
    return SandboxResult(
        candidate_id="candidate-performance",
        ran_successfully=success,
        row_count_before=before,
        row_count_after=after,
        execution_time_ms=elapsed_ms,
    )


class PerformanceMetricsTests(unittest.TestCase):
    def test_successful_execution_metrics(self):
        metrics = compute_performance_metrics(make_result())

        self.assertEqual(metrics.candidate_id, "candidate-performance")
        self.assertTrue(metrics.execution_succeeded)
        self.assertEqual(metrics.execution_time_ms, 12.5)
        self.assertTrue(metrics.row_count_preserved)

    def test_failed_execution_metrics(self):
        metrics = compute_performance_metrics(
            make_result(success=False, before=500, after=0, elapsed_ms=2.0)
        )

        self.assertFalse(metrics.execution_succeeded)
        self.assertEqual(metrics.input_row_count, 500)
        self.assertEqual(metrics.output_row_count, 0)
        self.assertEqual(metrics.row_count_difference, -500)
        self.assertFalse(metrics.row_count_preserved)

    def test_execution_time_is_non_negative(self):
        metrics = compute_performance_metrics(make_result(elapsed_ms=0.0))

        self.assertGreaterEqual(metrics.execution_time_ms, 0)

    def test_input_and_output_row_counts_are_preserved_correctly(self):
        metrics = compute_performance_metrics(make_result(before=10, after=10))

        self.assertEqual(metrics.input_row_count, 10)
        self.assertEqual(metrics.output_row_count, 10)
        self.assertEqual(metrics.row_count_difference, 0)
        self.assertTrue(metrics.row_count_preserved)

    def test_zero_row_input_is_handled(self):
        metrics = compute_performance_metrics(make_result(before=0, after=0))

        self.assertEqual(metrics.input_row_count, 0)
        self.assertEqual(metrics.output_row_count, 0)
        self.assertEqual(metrics.row_count_difference, 0)
        self.assertTrue(metrics.row_count_preserved)

    def test_metrics_use_existing_sandbox_result(self):
        sandbox = make_result(before=3, after=2, elapsed_ms=4.25)
        metrics = compute_performance_metrics(sandbox)

        self.assertIs(metrics.sandbox_result, sandbox)
        self.assertEqual(metrics.output_row_count, sandbox.row_count_after)
        self.assertEqual(metrics.execution_time_ms, sandbox.execution_time_ms)


if __name__ == "__main__":
    unittest.main()
