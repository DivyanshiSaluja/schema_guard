"""Normalize schema drift into typed events.

A correspondence between prior and changed columns is treated as a
hypothesis. It is accepted only when key-aligned probe data supports it with
a statistical lower bound: the Wilson score lower bound of the agreement rate
must reach ``theta``. A dropped column matched by one added column is a
RENAME; one dropped column reproduced by joining two added columns is a
SPLIT; two dropped columns whose join reproduces one added column is a
MERGE. More than one confirmed match is AMBIGUOUS, and nothing confirmed
leaves a DROP plus an ADD, so no backward mapping is ever guessed.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import permutations
from typing import Any


RENAME = "RENAME"
ADD = "ADD"
DROP = "DROP"
RETYPE = "RETYPE"
SPLIT = "SPLIT"
MERGE = "MERGE"
AMBIGUOUS = "AMBIGUOUS"

DEFAULT_THETA = 0.9
DEFAULT_Z = 1.96  # two-sided 95% confidence
SEPARATORS = (" ", ", ", ",", "-", "_", "|")


@dataclass(frozen=True)
class DriftEvent:
    table: str
    drift_class: str
    old_column: str | None = None
    new_column: str | None = None
    old_type: str | None = None
    new_type: str | None = None
    candidates: tuple[str, ...] = ()
    match_ratio: float | None = None
    parts: tuple[str, ...] = ()
    separator: str | None = None
    support: int = 0
    lower_bound: float | None = None


def wilson_lower_bound(successes: int, trials: int, z: float = DEFAULT_Z) -> float:
    """Lower end of the Wilson score interval for a binomial proportion."""

    if trials == 0:
        return 0.0
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = p + z * z / (2 * trials)
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return (centre - margin) / denominator


def required_probe_size(theta: float = DEFAULT_THETA, z: float = DEFAULT_Z) -> int:
    """Smallest probe set whose perfect agreement clears ``theta``."""

    n = 1
    while wilson_lower_bound(n, n, z) < theta:
        n += 1
    return n


def normalize_drift(
    table: str,
    old_schema: Mapping[str, str],
    new_schema: Mapping[str, str],
    *,
    probe_rows: Iterable[Mapping[str, Any]] = (),
    live_rows: Iterable[Mapping[str, Any]] = (),
    key: str = "id",
    theta: float = DEFAULT_THETA,
    z: float = DEFAULT_Z,
) -> tuple[DriftEvent, ...]:
    """Return drift events for one table, confirming correspondences on probes."""

    old_cols, new_cols = set(old_schema), set(new_schema)
    dropped = sorted(old_cols - new_cols)
    added = sorted(new_cols - old_cols)
    pairs = _aligned_pairs(probe_rows, live_rows, key)

    def confirm(before: Callable, after: Callable) -> tuple[int, int, float]:
        trials = successes = 0
        for prior, changed in pairs:
            try:
                left, right = before(prior), after(changed)
            except KeyError:
                continue
            trials += 1
            successes += left == right
        return successes, trials, wilson_lower_bound(successes, trials, z)

    events: list[DriftEvent] = []
    used_old: set[str] = set()
    used_new: set[str] = set()

    # Renames: one prior column carried by one changed column.
    for old in dropped:
        matches = []
        for new in added:
            ok, n, lb = confirm(lambda r, c=old: r[c], lambda r, c=new: r[c])
            if lb >= theta:
                matches.append((new, ok, n, lb))
        if len(matches) == 1:
            new, ok, n, lb = matches[0]
            used_old.add(old)
            used_new.add(new)
            events.append(DriftEvent(
                table, RENAME, old, new, old_schema[old], new_schema[new],
                match_ratio=ok / n, support=n, lower_bound=lb,
            ))
        elif matches:
            used_old.add(old)
            used_new.update(new for new, *_ in matches)
            events.append(DriftEvent(
                table, AMBIGUOUS, old_column=old, old_type=old_schema[old],
                candidates=tuple(new for new, *_ in matches),
            ))

    # Splits: one prior column reproduced by joining two changed columns.
    for old in [c for c in dropped if c not in used_old]:
        free = [c for c in added if c not in used_new]
        for first, second in permutations(free, 2):
            hit = _first_separator(
                confirm, theta,
                lambda r, c=old: r[c], None,
                (first, second), joined_side="after",
            )
            if hit is not None:
                sep, ok, n, lb = hit
                used_old.add(old)
                used_new.update((first, second))
                events.append(DriftEvent(
                    table, SPLIT, old_column=old, old_type=old_schema[old],
                    parts=(first, second), separator=sep,
                    match_ratio=ok / n, support=n, lower_bound=lb,
                ))
                break

    # Merges: two prior columns whose join reproduces one changed column.
    for new in [c for c in added if c not in used_new]:
        free = [c for c in dropped if c not in used_old]
        for first, second in permutations(free, 2):
            hit = _first_separator(
                confirm, theta, None, lambda r, c=new: r[c],
                (first, second), joined_side="before",
            )
            if hit is not None:
                sep, ok, n, lb = hit
                used_new.add(new)
                used_old.update((first, second))
                events.append(DriftEvent(
                    table, MERGE, new_column=new, new_type=new_schema[new],
                    parts=(first, second), separator=sep,
                    match_ratio=ok / n, support=n, lower_bound=lb,
                ))
                break

    for old in dropped:
        if old not in used_old:
            events.append(DriftEvent(table, DROP, old_column=old, old_type=old_schema[old]))
    for new in added:
        if new not in used_new:
            events.append(DriftEvent(table, ADD, new_column=new, new_type=new_schema[new]))
    for column in sorted(old_cols & new_cols):
        if old_schema[column] != new_schema[column]:
            events.append(DriftEvent(
                table, RETYPE, column, column, old_schema[column], new_schema[column],
            ))
    return tuple(events)


def _first_separator(confirm, theta, single_before, single_after, parts, joined_side):
    first, second = parts
    for sep in SEPARATORS:
        joined = lambda r, s=sep: f"{r[first]}{s}{r[second]}"
        if joined_side == "after":
            ok, n, lb = confirm(single_before, joined)
        else:
            ok, n, lb = confirm(joined, single_after)
        if lb >= theta:
            return sep, ok, n, lb
    return None


def _aligned_pairs(probe_rows, live_rows, key) -> list[tuple[Mapping, Mapping]]:
    live_by_key = {row[key]: row for row in live_rows if key in row}
    return [
        (row, live_by_key[row[key]])
        for row in probe_rows
        if key in row and row[key] in live_by_key
    ]
