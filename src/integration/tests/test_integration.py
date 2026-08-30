import json
import tempfile
import unittest
from pathlib import Path

from src.common.models import RepairCandidate
from src.integration.pipeline import load_candidates_json, run_integration


FIXTURE = Path(__file__).with_name("mock_candidates.json")
ORIGINAL_CODE = "def transform(row):\n    return {'original': True}\n"
APPROVED_CODE = "def transform(row):\n    return {'approved': True}\n"


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.source_path = root / "transformations.py"
        self.snapshot_dir = root / "snapshots"
        self.source_path.write_text(ORIGINAL_CODE, encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_candidate_approval_and_deployment(self):
        result = run_integration(
            FIXTURE,
            approved_candidate_id="candidate-valid",
            etl_runner=lambda: True,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertEqual(result.error, "")
        self.assertTrue(result.deployment.deployed)
        self.assertEqual(
            self.source_path.read_text(encoding="utf-8"),
            json.loads(FIXTURE.read_text())[0]["candidate"]["code"],
        )

    def test_invalid_candidate_is_blocked_from_approval(self):
        result = run_integration(
            FIXTURE,
            approved_candidate_id="candidate-invalid",
            etl_runner=lambda: True,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertIn("data-quality", result.error)
        self.assertIsNone(result.deployment)
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), ORIGINAL_CODE)

    def test_multiple_candidates_are_ranked_and_top_candidate_selected(self):
        result = run_integration(FIXTURE)

        self.assertEqual(
            [report.candidate_id for report in result.reports],
            ["candidate-valid", "candidate-invalid"],
        )
        self.assertEqual(result.reports[0].ranked_position, 1)
        self.assertEqual(result.reports[0].confidence_score, 1.0)
        selected = result.review_state.items[0]
        self.assertEqual(selected.candidate_id, "candidate-valid")

    def test_deployment_failure_triggers_rollback(self):
        calls = []

        def etl_runner():
            calls.append(self.source_path.read_text(encoding="utf-8"))
            return len(calls) > 1

        result = run_integration(
            FIXTURE,
            approved_candidate_id="candidate-valid",
            etl_runner=etl_runner,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertFalse(result.deployment.deployed)
        self.assertIsNotNone(result.rollback)
        self.assertTrue(result.rollback.rollback_succeeded)
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), ORIGINAL_CODE)
        self.assertEqual(len(calls), 2)

    def test_all_candidates_rejected_means_no_deployment(self):
        result = run_integration(
            FIXTURE,
            rejected_candidate_ids=("candidate-valid", "candidate-invalid"),
        )
        self.assertIsNone(result.deployment)
        self.assertIsNone(result.rollback)
        self.assertIsNone(result.review_state.approved_candidate_id)

    def test_malformed_candidates_json_returns_graceful_error(self):
        malformed = Path(self.temp_dir.name) / "malformed.json"
        malformed.write_text("not valid json", encoding="utf-8")

        result = run_integration(malformed)

        self.assertIn("could not load candidates JSON", result.error)
        self.assertEqual(result.reports, ())

    def test_json_deserializes_existing_dataclasses(self):
        inputs = load_candidates_json(FIXTURE)

        self.assertIsInstance(inputs[0].candidate, RepairCandidate)
        self.assertEqual(inputs[0].sandbox_result.candidate_id, "candidate-valid")
        self.assertEqual(len(inputs[0].output_rows), 2)

    def test_candidate_ids_and_validation_results_survive_flow(self):
        result = run_integration(FIXTURE)

        self.assertEqual(
            {item.candidate_id for item in result.review_state.items},
            {"candidate-valid", "candidate-invalid"},
        )
        invalid = next(
            report for report in result.reports if report.candidate_id == "candidate-invalid"
        )
        self.assertFalse(invalid.data_quality_ok)
        self.assertEqual(invalid.confidence_score, 0.0)


if __name__ == "__main__":
    unittest.main()
