"""Pure data-quality checks for sandboxed warehouse customer output."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.common.models import SandboxResult


REQUIRED_COLUMNS = ("id", "name", "email")
EXPECTED_TYPES = {"id": int, "name": str, "email": str}


@dataclass(frozen=True)
class DataQualityResult:
    """Detailed outcome of validating one sandbox output dataset."""

    candidate_id: str
    data_quality_ok: bool
    row_count_ok: bool
    required_columns_ok: bool
    null_values_ok: bool
    duplicate_ids_ok: bool
    schema_ok: bool
    errors: tuple[str, ...] = ()


def _is_compatible(value: Any, expected_type: type) -> bool:
    """Return whether a value matches the expected warehouse type."""

    # bool is a subclass of int, but is not a valid customer ID here.
    return value is not None and type(value) is expected_type


def validate_data_quality(
    rows: Iterable[Mapping[str, Any]],
    sandbox_result: SandboxResult,
) -> DataQualityResult:
    """Validate transformed rows without making database calls.

    ``rows`` should be the exact transformed output that was measured by the
    sandbox. The sandbox result is required so an unsuccessful candidate can
    never pass downstream validation.
    """

    materialized_rows = list(rows)
    errors: list[str] = []

    sandbox_ok = sandbox_result.ran_successfully
    if not sandbox_ok:
        errors.append("sandbox execution failed")

    row_count_ok = (
        sandbox_result.row_count_before == sandbox_result.row_count_after
        and len(materialized_rows) == sandbox_result.row_count_after
    )
    if not row_count_ok:
        errors.append(
            "row count mismatch: "
            f"before={sandbox_result.row_count_before}, "
            f"after={sandbox_result.row_count_after}, "
            f"provided={len(materialized_rows)}"
        )

    missing_columns = [
        column
        for column in REQUIRED_COLUMNS
        if any(column not in row for row in materialized_rows)
    ]
    required_columns_ok = not missing_columns
    if missing_columns:
        errors.append("missing required columns: " + ", ".join(missing_columns))

    null_values = [
        column
        for column in REQUIRED_COLUMNS
        if any(column in row and row[column] is None for row in materialized_rows)
    ]
    null_values_ok = not null_values
    if null_values:
        errors.append("NULL values in required columns: " + ", ".join(null_values))

    ids = [row["id"] for row in materialized_rows if "id" in row]
    duplicate_ids_ok = all(
        current_id not in ids[:index] for index, current_id in enumerate(ids)
    )
    if not duplicate_ids_ok:
        errors.append("duplicate customer IDs detected")

    incompatible_values = []
    for row_number, row in enumerate(materialized_rows, start=1):
        for column, expected_type in EXPECTED_TYPES.items():
            if column in row and row[column] is not None and not _is_compatible(
                row[column], expected_type
            ):
                incompatible_values.append(
                    f"row {row_number} column '{column}' expected {expected_type.__name__}"
                )
    schema_ok = not incompatible_values
    if incompatible_values:
        errors.append("incompatible types: " + "; ".join(incompatible_values))

    overall_ok = (
        sandbox_ok
        and row_count_ok
        and required_columns_ok
        and null_values_ok
        and duplicate_ids_ok
        and schema_ok
    )
    return DataQualityResult(
        candidate_id=sandbox_result.candidate_id,
        data_quality_ok=overall_ok,
        row_count_ok=row_count_ok,
        required_columns_ok=required_columns_ok,
        null_values_ok=null_values_ok,
        duplicate_ids_ok=duplicate_ids_ok,
        schema_ok=schema_ok,
        errors=tuple(errors),
    )
