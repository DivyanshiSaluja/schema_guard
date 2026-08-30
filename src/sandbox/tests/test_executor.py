import unittest

from src.common.models import RepairCandidate
from src.sandbox.executor import execute_candidate


VALID_CODE = """\
def transform(row):
    return {
        "id": row["id"],
        "name": row["full_name"],
        "email": row["email"],
    }
"""


class SandboxExecutorTests(unittest.TestCase):
    def test_valid_candidate_succeeds(self):
        result = execute_candidate(RepairCandidate("valid", VALID_CODE, "rename"))

        self.assertEqual(result.candidate_id, "valid")
        self.assertTrue(result.ran_successfully)
        self.assertTrue(result.execution_time_ms >= 0)

    def test_row_counts_are_measured(self):
        result = execute_candidate(
            RepairCandidate("counts", VALID_CODE, "rename"),
            [{"id": 1, "full_name": "A", "email": "a@example.com"}] * 3,
        )

        self.assertEqual(result.row_count_before, 3)
        self.assertEqual(result.row_count_after, 3)

    def test_invalid_candidate_fails_gracefully_and_captures_error(self):
        candidate = RepairCandidate(
            "invalid",
            "def transform(row):\n    return row[\"missing\"]",
            "bad field",
        )

        result = execute_candidate(candidate)

        self.assertFalse(result.ran_successfully)
        self.assertEqual(result.candidate_id, "invalid")
        self.assertEqual(result.row_count_before, 2)
        self.assertIn("KeyError", result.error_log)
        self.assertFalse(candidate.passed_sandbox)
        self.assertEqual(candidate.sandbox_log, result.error_log)

    def test_dangerous_code_is_rejected_without_database_access(self):
        candidate = RepairCandidate(
            "unsafe",
            "import os\ndef transform(row):\n    return row",
            "unsafe",
        )

        result = execute_candidate(candidate)

        self.assertFalse(result.ran_successfully)
        self.assertIn("candidate must define exactly one transform function", result.error_log)


if __name__ == "__main__":
    unittest.main()
