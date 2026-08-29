import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.review.dashboard import (
    CANDIDATES_PATH_ENV,
    DEFAULT_CANDIDATES_PATH,
    load_dashboard_items,
    resolve_candidates_path,
)


class DashboardLoadingTests(unittest.TestCase):
    def test_default_path_points_to_integration_fixture(self):
        self.assertEqual(resolve_candidates_path(), DEFAULT_CANDIDATES_PATH)
        self.assertTrue(DEFAULT_CANDIDATES_PATH.is_file())

    def test_default_fixture_loads_review_items(self):
        items = load_dashboard_items()

        self.assertEqual(
            [item.candidate_id for item in items],
            ["candidate-valid", "candidate-invalid"],
        )

    def test_explicit_path_is_configurable(self):
        self.assertEqual(resolve_candidates_path("custom/candidates.json"), Path("custom/candidates.json"))

    def test_environment_path_is_configurable(self):
        original = os.environ.get(CANDIDATES_PATH_ENV)
        try:
            os.environ[CANDIDATES_PATH_ENV] = "configured/candidates.json"
            self.assertEqual(resolve_candidates_path(), Path("configured/candidates.json"))
        finally:
            if original is None:
                os.environ.pop(CANDIDATES_PATH_ENV, None)
            else:
                os.environ[CANDIDATES_PATH_ENV] = original

    def test_malformed_dashboard_input_fails_gracefully(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("not json", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_dashboard_items(path)


if __name__ == "__main__":
    unittest.main()
