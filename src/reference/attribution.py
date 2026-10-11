"""Post-deployment attribution of hard-invariant violations.

Each live batch is run through the deployed candidate C and the reference
path R. A violation that only C produces is candidate-induced and the
reference path, which is valid on the changed schema, is activated. A
violation both produce is upstream-induced and the offending keys are
quarantined. If the incumbent transformation already violated an invariant
on pre-drift data, R is not trusted for that invariant and nothing is rolled
back automatically.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.reference.drift import DriftEvent
from src.reference.invariants import Invariant, violations
from src.reference.mapping import derive_backward_mapping
from src.reference.verify import ReferencePath, run_candidate
from src.sandbox.executor import _compile_transform


NO_FAULT = "NO_FAULT"
CANDIDATE_INDUCED = "CANDIDATE_INDUCED"
UPSTREAM_INDUCED = "UPSTREAM_INDUCED"
REFERENCE_DEFICIENT = "REFERENCE_DEFICIENT"
INDETERMINATE = "INDETERMINATE"

CONTINUE = "CONTINUE"
ACTIVATE_REFERENCE = "ACTIVATE_REFERENCE"
QUARANTINE = "QUARANTINE"
HALT_FIELD = "HALT_FIELD"
ALERT = "ALERT"

_PRIORITY = [ACTIVATE_REFERENCE, HALT_FIELD, QUARANTINE, ALERT, CONTINUE]


@dataclass(frozen=True)
class InvariantAttribution:
    invariant: str
    attribution: str
    action: str
    candidate_keys: tuple[Any, ...]
    reference_keys: tuple[Any, ...]


@dataclass(frozen=True)
class AttributionReport:
    per_invariant: tuple[InvariantAttribution, ...]
    action: str
    quarantined_keys: tuple[Any, ...]
    reference_rows: int = 0


def in_sample(key: Any, fraction: float) -> bool:
    """Deterministic, reproducible membership of a key in the replay sample."""

    if fraction >= 1.0:
        return True
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < fraction


def candidate_share_upper_bound(shared_only: int, alpha: float) -> float:
    """Exact upper confidence bound on the share of candidate-caused violations.

    When ``shared_only`` sampled violating records were all reproduced by the
    reference path, the Clopper-Pearson upper bound at level 1 - alpha on the
    fraction of violations the candidate alone would cause is
    1 - alpha ** (1 / shared_only).
    """

    if shared_only == 0:
        return 1.0
    return 1 - alpha ** (1 / shared_only)


def incumbent_health(
    incumbent_code: str,
    probe_rows: Sequence[Mapping[str, Any]],
    invariants: Sequence[Invariant],
    *,
    key: str = "id",
) -> frozenset[str]:
    """Names of invariants the incumbent already violated on pre-drift data."""

    transform = _compile_transform(incumbent_code)
    outputs = []
    for row in probe_rows:
        out, _, _ = run_candidate(transform, row)
        if out is not None:
            outputs.append((row.get(key), out))
    return frozenset(inv.name for inv in invariants if violations(inv, outputs))


def attribute_batch(
    batch: Sequence[Mapping[str, Any]],
    candidate_code: str,
    incumbent_code: str,
    events: tuple[DriftEvent, ...],
    invariants: Sequence[Invariant],
    *,
    key: str = "id",
    untrusted_invariants: frozenset[str] = frozenset(),
    sample_fraction: float = 1.0,
    alpha: float = 0.05,
    max_candidate_share: float = 0.25,
) -> AttributionReport:
    """Attribute violations in one batch.

    With ``sample_fraction`` below 1 the reference path is replayed only on a
    deterministic sample of keys. A sampled record the candidate violates and
    the reference satisfies proves a candidate fault outright. Violations are
    judged upstream only when every sampled violation is shared and the exact
    upper bound on the candidate's share stays below ``max_candidate_share``;
    otherwise the field is halted.
    """

    candidate = _compile_transform(candidate_code)
    reference = ReferencePath(incumbent_code, derive_backward_mapping(events))

    cand_outputs, ref_outputs = [], []
    sampled: set[Any] = set()
    unverifiable: set[str] = set()
    for row in batch:
        out, error, _ = run_candidate(candidate, row)
        # A candidate that raises produces no row: every field counts as missing.
        cand_outputs.append((row.get(key), out if out is not None else {}))
        if not in_sample(row.get(key), sample_fraction):
            continue
        sampled.add(row.get(key))
        ref_out, row_unverifiable, _ = reference.run(row)
        unverifiable |= row_unverifiable
        ref_outputs.append((row.get(key), ref_out))

    results = []
    quarantined: set[Any] = set()
    for invariant in invariants:
        c_keys = violations(invariant, cand_outputs)
        evaluable = (
            invariant.field not in unverifiable
            and invariant.name not in untrusted_invariants
        )
        r_keys = violations(invariant, ref_outputs) if evaluable else ()
        if sample_fraction >= 1.0:
            attribution, action = _decide(c_keys, r_keys, evaluable)
            # Records both paths fail are bad input whatever else is decided.
            shared = set(c_keys) & set(r_keys)
        else:
            attribution, action = _decide_sampled(
                c_keys, r_keys, sampled, evaluable, alpha, max_candidate_share
            )
            # Judged upstream on the sample: every candidate violation is held.
            shared = set(c_keys) if action == QUARANTINE else set(c_keys) & set(r_keys)
        if action in (QUARANTINE, ACTIVATE_REFERENCE):
            quarantined |= shared
        results.append(
            InvariantAttribution(invariant.name, attribution, action, c_keys, r_keys)
        )

    overall = min((r.action for r in results), key=_PRIORITY.index, default=CONTINUE)
    return AttributionReport(
        tuple(results), overall, tuple(sorted(quarantined, key=str)), len(ref_outputs)
    )


def _decide_sampled(c_keys, r_keys, sampled, evaluable, alpha, max_share) -> tuple[str, str]:
    if not c_keys:
        return (REFERENCE_DEFICIENT, ALERT) if evaluable and r_keys else (NO_FAULT, CONTINUE)
    if not evaluable:
        return INDETERMINATE, HALT_FIELD
    c_sampled = {k for k in c_keys if k in sampled}
    if c_sampled - set(r_keys):
        return CANDIDATE_INDUCED, ACTIVATE_REFERENCE
    if c_sampled and candidate_share_upper_bound(len(c_sampled), alpha) <= max_share:
        return UPSTREAM_INDUCED, QUARANTINE
    return INDETERMINATE, HALT_FIELD


def _decide(c_keys, r_keys, evaluable) -> tuple[str, str]:
    if not c_keys:
        if evaluable and r_keys:
            return REFERENCE_DEFICIENT, ALERT
        return NO_FAULT, CONTINUE
    if not evaluable:
        return INDETERMINATE, HALT_FIELD
    if set(c_keys) - set(r_keys):
        return CANDIDATE_INDUCED, ACTIVATE_REFERENCE
    return UPSTREAM_INDUCED, QUARANTINE
