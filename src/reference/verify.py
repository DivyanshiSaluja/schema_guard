"""Verify a candidate transformation against the drift-derived reference path.

The reference path R is the incumbent transformation T composed with the
backward mapping. The candidate C must agree with R on every target field,
except fields whose reference depends on input the mapping cannot recover;
those fields are reported as UNVERIFIABLE instead of passing silently.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.reference.drift import MERGE, RENAME, SPLIT, DriftEvent
from src.reference.invariants import UNIQUE, Invariant, violations
from src.reference.mapping import BackwardMapping, derive_backward_mapping
from src.reference.readset import RecordingRow, field_dependencies, static_reads
from src.sandbox.executor import _compile_transform


EQUIVALENT = "EQUIVALENT"
DIVERGENT = "DIVERGENT"
UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True)
class Divergence:
    key: Any
    field: str
    kind: str  # "value", "missing", "extra" or "error"
    candidate_value: Any = None
    reference_value: Any = None


@dataclass(frozen=True)
class Witness:
    rows: tuple[dict[str, Any], ...]
    signature: tuple[str, str]
    candidate_reads: frozenset[str]
    reference_reads: frozenset[str]
    minimal: bool

    @property
    def read_difference(self) -> dict[str, frozenset[str]]:
        return {
            "candidate_only": self.candidate_reads - self.reference_reads,
            "reference_only": self.reference_reads - self.candidate_reads,
        }


@dataclass(frozen=True)
class VerificationResult:
    candidate_id: str
    verdict: str
    divergences: tuple[Divergence, ...]
    unverifiable_fields: frozenset[str]
    invariant_violations: dict[str, tuple[Any, ...]]
    witness: Witness | None
    domain_size: int
    upstream_violations: dict[str, tuple[Any, ...]] = field(default_factory=dict)

    @property
    def deployable(self) -> bool:
        return self.verdict == EQUIVALENT and not self.invariant_violations

    @property
    def needs_waiver(self) -> bool:
        return self.verdict == UNVERIFIABLE and not self.invariant_violations


@dataclass
class ReferencePath:
    """R = T composed with the backward mapping."""

    incumbent_code: str
    mapping: BackwardMapping
    _transform: Callable = field(init=False, repr=False)
    unverifiable_fields: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        self._transform = _compile_transform(self.incumbent_code)
        deps = field_dependencies(self.incumbent_code)
        lost = self.mapping.unrecoverable
        if deps is None:
            reads = static_reads(self.incumbent_code)
            self._deps = None
            self.unverifiable_fields = frozenset()
            self._all_unverifiable = bool(reads & lost)
        else:
            self._deps = deps
            self._all_unverifiable = False
            self.unverifiable_fields = frozenset(
                name for name, sources in deps.items() if sources & lost
            )

    def run(self, row: Mapping[str, Any]) -> tuple[dict[str, Any], frozenset[str], set[str]]:
        """Return reference output, fields unverifiable on this row, and reads."""

        prior, lost = self.mapping.apply(row)
        recorder = RecordingRow(prior)
        output = dict(self._transform(recorder))
        if self._all_unverifiable:
            return output, frozenset(output), self._to_changed_names(recorder.reads)
        row_unverifiable = set(self.unverifiable_fields)
        if self._deps is not None and lost:
            row_unverifiable |= {
                name for name, sources in self._deps.items() if sources & lost
            }
        return output, frozenset(row_unverifiable), self._to_changed_names(recorder.reads)

    def _to_changed_names(self, reads: set[str]) -> set[str]:
        """Express prior-schema reads in changed-schema column names."""

        names: dict[str, tuple[str, ...]] = {}
        for event in self.mapping.events:
            if event.drift_class == RENAME:
                names[event.old_column] = (event.new_column,)
            elif event.drift_class == SPLIT:
                names[event.old_column] = event.parts
            elif event.drift_class == MERGE:
                for part in event.parts:
                    names[part] = (event.new_column,)
        return {changed for name in reads for changed in names.get(name, (name,))}


def run_candidate(transform: Callable, row: Mapping[str, Any]):
    """Return (output or None, error text, reads) for one row."""

    recorder = RecordingRow(row)
    try:
        output = transform(recorder)
        if not isinstance(output, Mapping):
            raise TypeError("transform must return a mapping")
        return dict(output), "", set(recorder.reads)
    except Exception:
        return None, traceback.format_exc(limit=1), set(recorder.reads)


def compare_row(
    key: Any,
    candidate_out: dict[str, Any] | None,
    error: str,
    reference_out: dict[str, Any],
    unverifiable: frozenset[str],
    allow_new_outputs: bool,
) -> list[Divergence]:
    if candidate_out is None:
        return [Divergence(key, "*", "error", error.strip().splitlines()[-1], None)]
    found = []
    for name, expected in reference_out.items():
        if name in unverifiable:
            continue
        if name not in candidate_out:
            found.append(Divergence(key, name, "missing", None, expected))
        elif not _same(candidate_out[name], expected):
            found.append(Divergence(key, name, "value", candidate_out[name], expected))
    if not allow_new_outputs:
        for name in candidate_out.keys() - reference_out.keys():
            found.append(Divergence(key, name, "extra", candidate_out[name], None))
    return found


def verify_candidate(
    candidate_id: str,
    candidate_code: str,
    incumbent_code: str,
    events: tuple[DriftEvent, ...],
    domain: Sequence[Mapping[str, Any]],
    invariants: Sequence[Invariant] = (),
    *,
    key: str = "id",
    allow_new_outputs: bool = False,
) -> VerificationResult:
    reference = ReferencePath(incumbent_code, derive_backward_mapping(events))
    try:
        candidate = _compile_transform(candidate_code)
    except Exception as error:
        divergence = Divergence(None, "*", "error", f"rejected before execution: {error}")
        return VerificationResult(
            candidate_id, DIVERGENT, (divergence,), reference.unverifiable_fields,
            {}, None, len(domain),
        )

    divergences: list[Divergence] = []
    unverifiable: set[str] = set(reference.unverifiable_fields)
    outputs: list[tuple[Any, dict[str, Any]]] = []
    ref_outputs: list[tuple[Any, dict[str, Any]]] = []
    for row in domain:
        try:
            ref_out, row_unverifiable, _ = reference.run(row)
        except Exception:
            # The reference itself cannot be formed for this row: nothing on
            # it can be verified, so it is reported rather than compared.
            unverifiable.add("*")
            continue
        unverifiable |= row_unverifiable
        ref_outputs.append((row.get(key), ref_out))
        cand_out, error, _ = run_candidate(candidate, row)
        divergences.extend(
            compare_row(
                row.get(key), cand_out, error, ref_out, row_unverifiable,
                allow_new_outputs,
            )
        )
        if cand_out is not None:
            outputs.append((row.get(key), cand_out))

    # A violation counts against the candidate only where the reference path
    # satisfies the same invariant on the same row; violations both share come
    # from the input data and are reported separately.
    broken: dict[str, tuple[Any, ...]] = {}
    upstream: dict[str, tuple[Any, ...]] = {}
    for invariant in invariants:
        c_keys = violations(invariant, outputs)
        if not c_keys:
            continue
        evaluable = invariant.field not in unverifiable and "*" not in unverifiable
        r_keys = set(violations(invariant, ref_outputs)) if evaluable else set()
        own = tuple(k for k in c_keys if k not in r_keys)
        shared = tuple(k for k in c_keys if k in r_keys)
        if own:
            broken[invariant.name] = own
        if shared:
            upstream[invariant.name] = shared

    if divergences:
        verdict = DIVERGENT
    elif unverifiable:
        verdict = UNVERIFIABLE
    else:
        verdict = EQUIVALENT

    witness = None
    if divergences:
        witness = divergence_witness(
            divergences[0], domain, candidate, reference, key, allow_new_outputs
        )
    elif broken:
        invariant = next(inv for inv in invariants if inv.name in broken)
        witness = invariant_witness(
            invariant, domain, candidate, reference, key, broken[invariant.name]
        )

    return VerificationResult(
        candidate_id, verdict, tuple(divergences), frozenset(unverifiable),
        broken, witness, len(domain), upstream,
    )


def divergence_witness(first, domain, candidate, reference, key, allow_new_outputs):
    signature = (first.field, first.kind)

    def fails(rows):
        for row in rows:
            ref_out, row_unverifiable, _ = reference.run(row)
            cand_out, error, _ = run_candidate(candidate, row)
            found = compare_row(
                row.get(key), cand_out, error, ref_out, row_unverifiable,
                allow_new_outputs,
            )
            if any((item.field, item.kind) == signature for item in found):
                return True
        return False

    seed = [row for row in domain if row.get(key) == first.key][:1]
    return _finish_witness(seed, fails, signature, candidate, reference, key)


def invariant_witness(invariant, domain, candidate, reference, key, keys):
    def fails(rows):
        outputs = []
        for row in rows:
            out, _, _ = run_candidate(candidate, row)
            if out is not None:
                outputs.append((row.get(key), out))
        return bool(violations(invariant, outputs))

    # Closure: a single-row clause is witnessed by one row; a uniqueness clause
    # needs every row sharing one duplicated value, so minimization keeps two.
    keys = set(keys)
    if invariant.kind == UNIQUE:
        by_value: dict[Any, list] = {}
        for row in domain:
            if row.get(key) in keys:
                out, _, _ = run_candidate(candidate, row)
                by_value.setdefault((out or {}).get(invariant.field), []).append(row)
        seed = min(by_value.values(), key=len)
    else:
        seed = [next(row for row in domain if row.get(key) in keys)]
    return _finish_witness(
        seed, fails, (invariant.field, invariant.kind), candidate, reference, key
    )


def minimize(rows: list, fails: Callable[[list], bool], budget: int = 200) -> tuple[list, bool]:
    """Delete one row at a time while the failure persists (1-minimal)."""

    current = list(rows)
    changed = True
    while changed and budget > 0:
        changed = False
        for index in range(len(current)):
            budget -= 1
            trial = current[:index] + current[index + 1:]
            if trial and fails(trial):
                current = trial
                changed = True
                break
            if budget <= 0:
                return current, False
    return current, True


def _finish_witness(seed, fails, signature, candidate, reference, key):
    rows, is_minimal = minimize(seed, fails)
    cand_reads: set[str] = set()
    ref_reads: set[str] = set()
    for row in rows:
        cand_reads |= run_candidate(candidate, row)[2]
        ref_reads |= reference.run(row)[2]
    keep = cand_reads | ref_reads | {key}
    neutral = tuple(
        {name: (value if name in keep else _neutral(value)) for name, value in row.items()}
        for row in rows
    )
    return Witness(neutral, signature, frozenset(cand_reads), frozenset(ref_reads), is_minimal)


def _neutral(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        return "<redacted>"
    if isinstance(value, (int, float)):
        return type(value)(0)
    return None


def _same(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right
