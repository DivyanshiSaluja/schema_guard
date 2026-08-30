"""Deterministic performance metrics derived from sandbox execution."""

from __future__ import annotations

from dataclasses import dataclass

from src.common.models import SandboxResult


@dataclass(frozen=True)
class PerformanceMetrics:
    """A view of sandbox metrics with useful derived values.

    The underlying ``SandboxResult`` remains the single source of truth for
    captured execution data; this class only adds derived properties.
    """

    sandbox_result: SandboxResult

    @property
    def candidate_id(self) -> str:
        return self.sandbox_result.candidate_id

    @property
    def execution_time_ms(self) -> float:
        return self.sandbox_result.execution_time_ms

    @property
    def input_row_count(self) -> int:
        return self.sandbox_result.row_count_before

    @property
    def output_row_count(self) -> int:
        return self.sandbox_result.row_count_after

    @property
    def execution_succeeded(self) -> bool:
        return self.sandbox_result.ran_successfully

    @property
    def row_count_difference(self) -> int:
        return self.output_row_count - self.input_row_count

    @property
    def row_count_preserved(self) -> bool:
        return self.row_count_difference == 0


def compute_performance_metrics(sandbox_result: SandboxResult) -> PerformanceMetrics:
    """Create performance metrics from one existing sandbox result."""

    return PerformanceMetrics(sandbox_result=sandbox_result)
