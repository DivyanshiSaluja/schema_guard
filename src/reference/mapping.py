"""Backward row mapping derived from drift events.

The mapping converts a record in the changed schema into a record shaped like
the prior schema, so that the incumbent transformation can run on it. Fields
the mapping cannot recover are reported instead of being guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from src.reference.drift import ADD, AMBIGUOUS, DROP, MERGE, RENAME, RETYPE, SPLIT, DriftEvent


UNRECOVERABLE = "<unrecoverable>"

# (changed type, prior type) -> converter from a changed value to a prior value
INVERSE_CASTS: dict[tuple[str, str], Callable[[Any], Any]] = {
    ("text", "integer"): int,
    ("bigint", "integer"): int,
    ("integer", "bigint"): int,
    ("integer", "text"): str,
    ("double precision", "real"): float,
    ("numeric", "integer"): int,
}

# prior type -> converter from a prior value back to the changed type
FORWARD_CASTS: dict[str, Callable[[Any], Any]] = {
    "text": str,
    "integer": int,
    "bigint": int,
    "real": float,
    "double precision": float,
    "numeric": float,
}


@dataclass(frozen=True)
class BackwardMapping:
    events: tuple[DriftEvent, ...]
    unrecoverable: frozenset[str]

    @property
    def renamed(self) -> dict[str, str]:
        """Prior column name -> changed column name for confirmed renames."""

        return {
            event.old_column: event.new_column
            for event in self.events
            if event.drift_class == RENAME
        }

    def apply(self, row: Mapping[str, Any]) -> tuple[dict[str, Any], frozenset[str]]:
        """Return the prior-shaped row and the prior fields lost for this row."""

        out = dict(row)
        lost: set[str] = set(self.unrecoverable)
        for event in self.events:
            if event.drift_class == RENAME and event.new_column in out:
                out[event.old_column] = out.pop(event.new_column)
            elif event.drift_class == ADD:
                out.pop(event.new_column, None)
            elif event.drift_class == SPLIT:
                # Prior value = the two changed parts joined; always recoverable
                # unless a part is missing for this record.
                first, second = (out.pop(part, None) for part in event.parts)
                if first is None or second is None:
                    lost.add(event.old_column)
                else:
                    out[event.old_column] = f"{first}{event.separator}{second}"
            elif event.drift_class == MERGE:
                # Prior parts = the changed value split on the separator; only
                # recoverable when the separator occurs exactly once.
                value = out.pop(event.new_column, None)
                pieces = None if value is None else str(value).split(event.separator)
                if pieces is None or len(pieces) != 2:
                    lost.update(event.parts)
                else:
                    out[event.parts[0]], out[event.parts[1]] = pieces
            elif event.drift_class == RETYPE and event.old_column in out:
                value = out[event.old_column]
                restored = _inverse_cast(event, value)
                if restored is UNRECOVERABLE:
                    lost.add(event.old_column)
                out[event.old_column] = restored
        for column in lost:
            out[column] = UNRECOVERABLE
        return out, frozenset(lost)


def derive_backward_mapping(events: tuple[DriftEvent, ...]) -> BackwardMapping:
    unrecoverable = set()
    for event in events:
        if event.drift_class in (DROP, AMBIGUOUS):
            unrecoverable.add(event.old_column)
        elif event.drift_class == RETYPE and (
            (event.new_type, event.old_type) not in INVERSE_CASTS
        ):
            unrecoverable.add(event.old_column)
    return BackwardMapping(events, frozenset(unrecoverable))


def _inverse_cast(event: DriftEvent, value: Any) -> Any:
    """Cast back to the prior type; a lossy or failing cast is unrecoverable."""

    if value is None:
        return None
    inverse = INVERSE_CASTS[(event.new_type, event.old_type)]
    forward = FORWARD_CASTS.get(event.new_type)
    try:
        restored = inverse(value)
        if forward is not None and forward(restored) != value:
            return UNRECOVERABLE
        return restored
    except (TypeError, ValueError):
        return UNRECOVERABLE
