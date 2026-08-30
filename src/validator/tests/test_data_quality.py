import unittest

from src.common.models import SandboxResult
from src.validator.data_quality import validate_data_quality


def sandbox_result(row_count: int, *, success: bool = True, after: int | None = None):
    return SandboxResult(
        candidate_id="candidate-1",
        ran_successfully=success,
        row_count_before=row_count,
        row_count_after=row_count if after is None else after,
        execution_time_ms=1.5,
    )


def rows(count: int = 500):
    return [
        {"id": index, "name": f"Customer {index}", "email": f"customer{index}@example.com"}
        for index in range(1, count + 1)
    ]


class DataQualityTests(unittest.TestCase):
    def test_valid_500_row_transformed_dataset_passes(self):
        result = validate_data_quality(rows(), sandbox_result(500))

        self.assertTrue(result.data_quality_ok)
        self.assertEqual(result.errors, ())

    def test_row_count_mismatch_fails(self):
        result = validate_data_quality(rows(499), sandbox_result(500, after=499))

        self.assertFalse(result.data_quality_ok)
        self.assertFalse(result.row_count_ok)

    def test_missing_required_column_fails(self):
        data = rows(1)
        del data[0]["email"]

        result = validate_data_quality(data, sandbox_result(1))

        self.assertFalse(result.data_quality_ok)
        self.assertFalse(result.required_columns_ok)

    def test_null_required_value_fails(self):
        data = rows(1)
        data[0]["name"] = None

        result = validate_data_quality(data, sandbox_result(1))

        self.assertFalse(result.data_quality_ok)
        self.assertFalse(result.null_values_ok)

    def test_duplicate_id_fails(self):
        data = rows(2)
        data[1]["id"] = data[0]["id"]

        result = validate_data_quality(data, sandbox_result(2))

        self.assertFalse(result.data_quality_ok)
        self.assertFalse(result.duplicate_ids_ok)

    def test_wrong_type_fails(self):
        data = rows(1)
        data[0]["id"] = "1"

        result = validate_data_quality(data, sandbox_result(1))

        self.assertFalse(result.data_quality_ok)
        self.assertFalse(result.schema_ok)

    def test_sandbox_failure_fails(self):
        result = validate_data_quality(rows(2), sandbox_result(2, success=False))

        self.assertFalse(result.data_quality_ok)
        self.assertIn("sandbox execution failed", result.errors)

    def test_valid_warehouse_customer_output_passes(self):
        warehouse_output = [
            {"id": 1, "name": "Customer 1", "email": "customer1@example.com"},
            {"id": 2, "name": "Customer 2", "email": "customer2@example.com"},
        ]

        result = validate_data_quality(warehouse_output, sandbox_result(2))

        self.assertTrue(result.data_quality_ok)
        self.assertTrue(result.schema_ok)


if __name__ == "__main__":
    unittest.main()
