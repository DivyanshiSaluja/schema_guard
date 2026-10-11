"""Hard invariants evaluated on transformed target rows."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


NOT_NULL = "not_null"
NON_EMPTY = "non_empty"
UNIQUE = "unique"


@dataclass(frozen=True)
class Invariant:
    kind: str
    field: str

    @property
    def name(self) -> str:
        return f"{self.kind}({self.field})"


def violations(invariant: Invariant, outputs: Iterable[tuple[Any, dict]]) -> tuple[Any, ...]:
    """Return the source keys whose target rows violate the invariant."""

    outputs = list(outputs)
    if invariant.kind == NOT_NULL:
        bad = [key for key, row in outputs if row.get(invariant.field) is None]
    elif invariant.kind == NON_EMPTY:
        bad = [
            key for key, row in outputs
            if row.get(invariant.field) is None or row.get(invariant.field) == ""
        ]
    elif invariant.kind == UNIQUE:
        counts = Counter(row.get(invariant.field) for _, row in outputs)
        bad = [key for key, row in outputs if counts[row.get(invariant.field)] > 1]
    else:
        raise ValueError(f"unknown invariant kind: {invariant.kind}")
    return tuple(bad)
