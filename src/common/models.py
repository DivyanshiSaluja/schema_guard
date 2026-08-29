from dataclasses import dataclass, field
from typing import Optional

@dataclass
class SchemaDiff:
    table: str
    change_type: str        # "RENAME" | "DROP" | "TYPE_CHANGE"
    old_column: Optional[str] = None
    new_column: Optional[str] = None

@dataclass
class RepairCandidate:
    id: str
    code: str                # patched transformation function as text
    explanation: str
    passed_sandbox: bool = False
    attempt: int = 1
    sandbox_log: str = ""