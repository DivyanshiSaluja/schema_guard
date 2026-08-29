import tempfile
import unittest
from pathlib import Path

from src.common.models import RepairCandidate, SandboxResult, ValidationReport
from src.deploy.deployer import deploy_approved_candidate
from src.deploy.rollback import rollback_latest
from src.deploy.snapshot_manager import SnapshotManager
from src.review.review_api import (
    ReviewStatus,
    approve_candidate,
    build_review_items,
    get_review_state,
    reject_candidate,
)


ORIGINAL_CODE = "def transform(row):\n    return {'name': row['full_name']}\n"
APPROVED_CODE = "def transform(row):\n    return {'name': row['full_name'], 'id': row['id']}\n"


class DeploymentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.source_path = root / "transformations.py"
        self.snapshot_dir = root / "schema_snapshots"
        self.source_path.write_text(ORIGINAL_CODE, encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_approved_state(self, candidate_id="candidate-1", code=APPROVED_CODE):
        candidate = RepairCandidate(candidate_id, code, "approved test candidate")
        sandbox = SandboxResult(candidate_id, True, 2, 2, 1.0)
        report = ValidationReport(candidate_id, True, True, 1.0, 1)
        items = build_review_items([candidate], [report], [sandbox])
        return candidate, approve_candidate(get_review_state(items), candidate_id)

    def test_snapshot_current_transformation(self):
        manager = SnapshotManager(self.source_path, self.snapshot_dir)

        snapshot = manager.snapshot_current_pipeline()

        self.assertIsNotNone(snapshot)
        self.assertTrue(snapshot.is_file())
        self.assertEqual(snapshot.read_text(encoding="utf-8"), ORIGINAL_CODE)

    def test_snapshot_filename_contains_timestamp(self):
        snapshot = SnapshotManager(
            self.source_path, self.snapshot_dir
        ).snapshot_current_pipeline()

        self.assertRegex(snapshot.name, r"^transformations_\d{20}\.py$")

    def test_multiple_snapshots_can_coexist(self):
        manager = SnapshotManager(self.source_path, self.snapshot_dir)
        first = manager.snapshot_current_pipeline()
        self.source_path.write_text(APPROVED_CODE, encoding="utf-8")
        second = manager.snapshot_current_pipeline()

        self.assertNotEqual(first, second)
        self.assertEqual(len(list(self.snapshot_dir.glob("*.py"))), 2)

    def test_latest_snapshot_can_be_identified(self):
        manager = SnapshotManager(self.source_path, self.snapshot_dir)
        first = manager.snapshot_current_pipeline()
        self.source_path.write_text(APPROVED_CODE, encoding="utf-8")
        second = manager.snapshot_current_pipeline()

        self.assertEqual(manager.find_latest_snapshot(), second)
        self.assertNotEqual(first, second)

    def test_latest_snapshot_can_be_restored(self):
        manager = SnapshotManager(self.source_path, self.snapshot_dir)
        manager.snapshot_current_pipeline()
        self.source_path.write_text(APPROVED_CODE, encoding="utf-8")

        restored = manager.restore_latest_snapshot()

        self.assertIsNotNone(restored)
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), ORIGINAL_CODE)

    def test_missing_snapshot_directory_is_handled(self):
        manager = SnapshotManager(self.source_path, self.snapshot_dir)

        self.assertIsNone(manager.find_latest_snapshot())
        self.assertIsNone(manager.restore_latest_snapshot())

    def test_approved_candidate_deploys(self):
        candidate, state = self.make_approved_state()
        etl_calls = []

        result = deploy_approved_candidate(
            candidate,
            state,
            etl_runner=lambda: etl_calls.append("ran"),
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(result.deployed)
        self.assertTrue(result.etl_succeeded)
        self.assertEqual(etl_calls, ["ran"])

    def test_rejected_candidate_cannot_deploy(self):
        candidate, state = self.make_approved_state()
        rejected_state = reject_candidate(state, candidate.id)

        result = deploy_approved_candidate(
            candidate, rejected_state, source_path=self.source_path, snapshot_dir=self.snapshot_dir
        )

        self.assertFalse(result.deployed)
        self.assertIn("not approved", result.error_log)
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), ORIGINAL_CODE)

    def test_pending_candidate_cannot_deploy(self):
        candidate = RepairCandidate("pending", APPROVED_CODE, "pending")
        state = get_review_state(
            build_review_items(
                [candidate],
                [ValidationReport("pending", True, True, 1.0, 1)],
                [SandboxResult("pending", True, 2, 2, 1.0)],
            )
        )

        result = deploy_approved_candidate(
            candidate, state, source_path=self.source_path, snapshot_dir=self.snapshot_dir
        )

        self.assertFalse(result.deployed)
        self.assertIsNone(result.snapshot_path)

    def test_snapshot_is_created_before_deployment(self):
        candidate, state = self.make_approved_state()
        observed = []

        def etl_runner():
            observed.append(self.source_path.read_text(encoding="utf-8"))

        result = deploy_approved_candidate(
            candidate,
            state,
            etl_runner=etl_runner,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(result.snapshot_path.is_file())
        self.assertEqual(result.snapshot_path.read_text(encoding="utf-8"), ORIGINAL_CODE)
        self.assertEqual(observed, [APPROVED_CODE])

    def test_candidate_code_reaches_transformations_file(self):
        candidate, state = self.make_approved_state()

        deploy_approved_candidate(
            candidate,
            state,
            etl_runner=lambda: True,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertEqual(self.source_path.read_text(encoding="utf-8"), APPROVED_CODE)

    def test_successful_etl_produces_successful_deployment(self):
        candidate, state = self.make_approved_state()

        result = deploy_approved_candidate(
            candidate,
            state,
            etl_runner=lambda: True,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(result.deployed)
        self.assertTrue(result.etl_succeeded)

    def test_deployment_failure_is_reported(self):
        candidate, state = self.make_approved_state()

        result = deploy_approved_candidate(
            candidate,
            state,
            etl_runner=lambda: False,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertFalse(result.deployed)
        self.assertFalse(result.etl_succeeded)
        self.assertIsNotNone(result.snapshot_path)

    def test_rollback_restores_previous_transformation(self):
        candidate, state = self.make_approved_state()
        deployment = deploy_approved_candidate(
            candidate,
            state,
            etl_runner=lambda: False,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        result = rollback_latest(
            etl_runner=lambda: True,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(deployment.snapshot_path.is_file())
        self.assertTrue(result.rollback_succeeded)
        self.assertTrue(result.restored)
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), ORIGINAL_CODE)

    def test_rollback_reruns_etl(self):
        manager = SnapshotManager(self.source_path, self.snapshot_dir)
        manager.snapshot_current_pipeline()
        calls = []

        result = rollback_latest(
            etl_runner=lambda: calls.append(self.source_path.read_text(encoding="utf-8")),
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(result.rollback_succeeded)
        self.assertEqual(calls, [ORIGINAL_CODE])

    def test_missing_snapshot_rollback_is_handled(self):
        result = rollback_latest(
            etl_runner=lambda: True,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertFalse(result.rollback_succeeded)
        self.assertFalse(result.restored)
        self.assertIn("no valid snapshot", result.error_log)

    def test_failed_rollback_is_reported(self):
        manager = SnapshotManager(self.source_path, self.snapshot_dir)
        manager.snapshot_current_pipeline()

        result = rollback_latest(
            etl_runner=lambda: False,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(result.restored)
        self.assertFalse(result.etl_succeeded)
        self.assertFalse(result.rollback_succeeded)

    def test_snapshot_remains_after_restoration(self):
        manager = SnapshotManager(self.source_path, self.snapshot_dir)
        snapshot = manager.snapshot_current_pipeline()

        rollback_latest(
            etl_runner=lambda: True,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )

        self.assertTrue(snapshot.is_file())

    def test_source_fixture_is_restored_after_tests(self):
        original = self.source_path.read_text(encoding="utf-8")
        candidate, state = self.make_approved_state()
        deploy_approved_candidate(
            candidate,
            state,
            etl_runner=lambda: True,
            source_path=self.source_path,
            snapshot_dir=self.snapshot_dir,
        )
        SnapshotManager(self.source_path, self.snapshot_dir).restore_latest_snapshot()

        self.assertEqual(self.source_path.read_text(encoding="utf-8"), original)

    def test_existing_review_validation_behavior_is_unchanged(self):
        candidate, state = self.make_approved_state()

        self.assertEqual(state.items[0].status, ReviewStatus.APPROVED)
        self.assertTrue(state.items[0].report.schema_ok)
        self.assertTrue(state.items[0].report.data_quality_ok)


if __name__ == "__main__":
    unittest.main()
