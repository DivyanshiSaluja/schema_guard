from dataclasses import dataclass
from typing import Optional


@dataclass
class SchemaDiff:
    table: str
    change_type: str
    old_column: Optional[str] = None
    new_column: Optional[str] = None
    old_type: Optional[str] = None
    new_type: Optional[str] = None


@dataclass
class RepairCandidate:
    id: str
    code: str
    explanation: str
    passed_sandbox: bool = False
    attempt: int = 1
    sandbox_log: str = ""


@dataclass
class SandboxResult:
    candidate_id: str
    ran_successfully: bool
    row_count_before: int
    row_count_after: int
    execution_time_ms: float
    error_log: str = ""


@dataclass
class ValidationReport:
    candidate_id: str
    schema_ok: bool
    data_quality_ok: bool
    confidence_score: float
    ranked_position: int = 0
