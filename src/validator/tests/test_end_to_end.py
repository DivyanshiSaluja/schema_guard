import unittest
from dataclasses import replace

from src.common.models import RepairCandidate
from src.sandbox.executor import execute_candidate
from src.validator.data_quality import validate_data_quality
from src.validator.performance import compute_performance_metrics
from src.validator.ranking import rank_candidates
from src.validator.report import build_validation_report


VALID_CODE = """\
def transform(row):
    return {
        "id": row["id"],
        "name": row["full_name"],
        "email": row["email"],
    }
"""

INVALID_OUTPUT_CODE = """\
def transform(row):
    return {
        "id": row["id"],
        "email": row["email"],
    }
"""

INVALID_SANDBOX_CODE = "import os\ndef transform(row):\n    return row"

INPUT_ROWS = (
    {"id": 1, "full_name": "Ada Lovelace", "email": "ada@example.com"},
    {"id": 2, "full_name": "Grace Hopper", "email": "grace@example.com"},
    {"id": 3, "full_name": "Alan Turing", "email": "alan@example.com"},
)

VALID_OUTPUT = [
    {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com"},
    {"id": 2, "name": "Grace Hopper", "email": "grace@example.com"},
    {"id": 3, "name": "Alan Turing", "email": "alan@example.com"},
]


class EndToEndValidationTests(unittest.TestCase):
    def run_flow(
        self,
        candidate: RepairCandidate,
        output_rows,
        *,
        schema_ok: bool = True,
        sandbox_result_override=None,
    ):
        sandbox_result = execute_candidate(candidate, INPUT_ROWS)
        if sandbox_result_override is not None:
            sandbox_result = sandbox_result_override(sandbox_result)
        data_quality = validate_data_quality(output_rows, sandbox_result)
        performance = compute_performance_metrics(sandbox_result)
        report = build_validation_report(
            candidate.id,
            schema_ok,
            data_quality,
            sandbox_result,
            performance,
        )
        return sandbox_result, data_quality, performance, report

    def test_valid_candidate_passes_sandbox_and_data_quality(self):
        candidate = RepairCandidate("valid", VALID_CODE, "rename full_name")

        sandbox_result, data_quality, _, report = self.run_flow(candidate, VALID_OUTPUT)

        self.assertTrue(sandbox_result.ran_successfully)
        self.assertTrue(data_quality.data_quality_ok)
        self.assertTrue(report.data_quality_ok)

    def test_invalid_output_fails_data_quality(self):
        candidate = RepairCandidate("invalid-output", INVALID_OUTPUT_CODE, "drop name")
        invalid_output = [
            {"id": row["id"], "email": row["email"]} for row in INPUT_ROWS
        ]

        sandbox_result, data_quality, _, report = self.run_flow(candidate, invalid_output)

        self.assertTrue(sandbox_result.ran_successfully)
        self.assertFalse(data_quality.data_quality_ok)
        self.assertFalse(report.data_quality_ok)

    def test_sandbox_failure_is_propagated(self):
        candidate = RepairCandidate("sandbox-failure", INVALID_SANDBOX_CODE, "unsafe")

        sandbox_result, data_quality, _, report = self.run_flow(candidate, [])

        self.assertFalse(sandbox_result.ran_successfully)
        self.assertFalse(data_quality.data_quality_ok)
        self.assertFalse(report.data_quality_ok)

    def test_row_count_mismatch_is_detected(self):
        candidate = RepairCandidate("row-mismatch", VALID_CODE, "rename")

        _, data_quality, _, report = self.run_flow(
            candidate,
            VALID_OUTPUT,
            sandbox_result_override=lambda result: replace(
                result, row_count_after=result.row_count_after - 1
            ),
        )

        self.assertFalse(data_quality.row_count_ok)
        self.assertFalse(data_quality.data_quality_ok)
        self.assertFalse(report.data_quality_ok)

    def test_schema_failure_is_reflected_in_report(self):
        candidate = RepairCandidate("schema-failure", VALID_CODE, "rename")

        _, data_quality, _, report = self.run_flow(
            candidate, VALID_OUTPUT, schema_ok=False
        )

        self.assertTrue(data_quality.data_quality_ok)
        self.assertFalse(report.schema_ok)
        self.assertEqual(report.confidence_score, 0.0)

    def test_validation_report_is_generated_correctly(self):
        candidate = RepairCandidate("report-candidate", VALID_CODE, "rename")

        _, _, performance, report = self.run_flow(candidate, VALID_OUTPUT)

        self.assertEqual(report.candidate_id, "report-candidate")
        self.assertTrue(report.schema_ok)
        self.assertTrue(report.data_quality_ok)
        self.assertEqual(report.confidence_score, 1.0)
        self.assertEqual(report.ranked_position, 0)
        self.assertIsNotNone(performance)

    def test_multiple_candidates_are_ranked_together(self):
        valid = RepairCandidate("valid-candidate", VALID_CODE, "valid")
        invalid = RepairCandidate("invalid-candidate", INVALID_OUTPUT_CODE, "invalid")
        _, _, _, valid_report = self.run_flow(valid, VALID_OUTPUT)
        _, _, _, invalid_report = self.run_flow(
            invalid,
            [{"id": row["id"], "email": row["email"]} for row in INPUT_ROWS],
        )

        ranked = rank_candidates([invalid_report, valid_report])

        self.assertEqual(
            [item.candidate_id for item in ranked],
            ["valid-candidate", "invalid-candidate"],
        )

    def test_high_confidence_valid_candidate_precedes_failed_candidate(self):
        valid = RepairCandidate("a-valid", VALID_CODE, "valid")
        failed = RepairCandidate("z-failed", INVALID_SANDBOX_CODE, "failed")
        _, _, _, valid_report = self.run_flow(valid, VALID_OUTPUT)
        _, _, _, failed_report = self.run_flow(failed, [])

        ranked = rank_candidates([failed_report, valid_report])

        self.assertEqual(ranked[0].candidate_id, "a-valid")
        self.assertEqual(ranked[0].confidence_score, 1.0)
        self.assertEqual(ranked[1].confidence_score, 0.0)

    def test_ranked_positions_are_correct(self):
        candidates = [
            RepairCandidate("first", VALID_CODE, "valid"),
            RepairCandidate("second", INVALID_OUTPUT_CODE, "invalid"),
        ]
        reports = [
            self.run_flow(
                candidates[0], VALID_OUTPUT
            )[-1],
            self.run_flow(
                candidates[1],
                [{"id": row["id"], "email": row["email"]} for row in INPUT_ROWS],
            )[-1],
        ]

        ranked = rank_candidates(reports)

        self.assertEqual([item.ranked_position for item in ranked], [1, 2])

    def test_candidate_ids_are_preserved_throughout_pipeline(self):
        candidate = RepairCandidate("preserved-id", VALID_CODE, "valid")

        sandbox_result, data_quality, performance, report = self.run_flow(
            candidate, VALID_OUTPUT
        )
        ranked = rank_candidates([report])

        self.assertEqual(sandbox_result.candidate_id, candidate.id)
        self.assertEqual(data_quality.candidate_id, candidate.id)
        self.assertEqual(performance.candidate_id, candidate.id)
        self.assertEqual(report.candidate_id, candidate.id)
        self.assertEqual(ranked[0].candidate_id, candidate.id)

    def test_performance_metrics_are_available_from_sandbox_result(self):
        candidate = RepairCandidate("performance-id", VALID_CODE, "valid")

        sandbox_result, _, performance, _ = self.run_flow(candidate, VALID_OUTPUT)

        self.assertGreaterEqual(sandbox_result.execution_time_ms, 0)
        self.assertEqual(performance.execution_time_ms, sandbox_result.execution_time_ms)
        self.assertEqual(performance.input_row_count, len(INPUT_ROWS))
        self.assertEqual(performance.output_row_count, len(INPUT_ROWS))

    def test_flow_uses_only_in_memory_inputs(self):
        candidate = RepairCandidate("memory-only", VALID_CODE, "valid")

        sandbox_result, data_quality, _, report = self.run_flow(candidate, VALID_OUTPUT)

        self.assertTrue(sandbox_result.ran_successfully)
        self.assertTrue(data_quality.data_quality_ok)
        self.assertTrue(report.data_quality_ok)

    def test_functional_pipeline_results_are_deterministic(self):
        def run_once():
            candidate = RepairCandidate("deterministic", VALID_CODE, "valid")
            sandbox_result, data_quality, performance, report = self.run_flow(
                candidate, VALID_OUTPUT
            )
            ranked = rank_candidates([report])
            return (
                sandbox_result.candidate_id,
                sandbox_result.ran_successfully,
                sandbox_result.row_count_before,
                sandbox_result.row_count_after,
                data_quality.data_quality_ok,
                performance.row_count_preserved,
                ranked[0].candidate_id,
                ranked[0].confidence_score,
                ranked[0].ranked_position,
            )

        self.assertEqual(run_once(), run_once())


if __name__ == "__main__":
    unittest.main()
