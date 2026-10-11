"""ETL run with post-deployment attribution.

Every run replays the batch through the deployed candidate and through the
drift-derived reference path before anything is written:

* candidate-induced violation -> the reference path is activated as the
  transformation module and its output is loaded instead;
* upstream-induced violation  -> offending source rows go to a quarantine
  table and the rest of the batch is loaded with the candidate;
* indeterminate               -> nothing is written; the last good target
  state is kept.

The delete-and-reload of the target and the quarantine inserts happen in one
transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text

from src.deploy.activation import activate_module
from src.reference.artifact import render_reference_module
from src.reference.attribution import (
    ACTIVATE_REFERENCE,
    HALT_FIELD,
    AttributionReport,
    attribute_batch,
)
from src.reference.drift import DriftEvent
from src.reference.invariants import Invariant, violations
from src.reference.mapping import derive_backward_mapping
from src.reference.verify import ReferencePath, run_candidate
from src.sandbox.executor import _compile_transform


@dataclass(frozen=True)
class GuardedRunResult:
    action: str
    rows_written: int
    rows_quarantined: int
    reference_activated: bool
    report: AttributionReport
    note: str = ""


def run_guarded_etl(
    engine,
    *,
    candidate_code: str,
    incumbent_code: str,
    events: tuple[DriftEvent, ...],
    invariants: tuple[Invariant, ...],
    untrusted_invariants: frozenset[str] = frozenset(),
    sample_fraction: float = 1.0,
    source_table: str = "public.customers",
    target_table: str = "warehouse.customers",
    quarantine_table: str = "warehouse.customers_quarantine",
    active_module: Path | str | None = None,
    key: str = "id",
) -> GuardedRunResult:
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text(f"SELECT * FROM {source_table}")).mappings()]

    report = attribute_batch(
        rows, candidate_code, incumbent_code, events, invariants,
        key=key, untrusted_invariants=untrusted_invariants,
        sample_fraction=sample_fraction,
    )

    if report.action == HALT_FIELD:
        return GuardedRunResult(HALT_FIELD, 0, 0, False, report, "writes halted")

    reference = ReferencePath(incumbent_code, derive_backward_mapping(events))
    use_reference = report.action == ACTIVATE_REFERENCE
    if use_reference:
        lost_anywhere = any(reference.run(row)[1] for row in rows)
        if lost_anywhere:
            # The reference cannot produce every field for this batch, so it is
            # not a safe fallback; keep the last good target state instead.
            return GuardedRunResult(
                HALT_FIELD, 0, 0, False, report,
                "reference not defined for every field; writes halted",
            )
        if active_module is not None:
            activate_module(render_reference_module(incumbent_code, events), active_module)

    transform = None if use_reference else _compile_transform(candidate_code)
    quarantined = set(report.quarantined_keys)
    reasons: dict = {}
    if use_reference:
        # Once the reference path is active, any record it cannot load within
        # the invariants is bad input, including records outside the sample.
        ref_outputs = [(row.get(key), reference.run(row)[0]) for row in rows]
        for invariant in invariants:
            for k in violations(invariant, ref_outputs):
                quarantined.add(k)
                reasons.setdefault(k, invariant.name)
    for item in report.per_invariant:
        for k in item.candidate_keys:
            if k in quarantined:
                reasons.setdefault(k, item.invariant)

    outputs = []
    for row in rows:
        if row.get(key) in quarantined:
            continue
        out = reference.run(row)[0] if use_reference else run_candidate(transform, row)[0]
        if out is not None:
            outputs.append(out)

    with engine.begin() as conn:
        conn.execute(text(
            f"CREATE TABLE IF NOT EXISTS {quarantine_table} ("
            "source_key TEXT, invariant TEXT, payload JSONB, "
            "quarantined_at TIMESTAMPTZ DEFAULT now())"
        ))
        conn.execute(text(f"DELETE FROM {target_table}"))
        if outputs:
            columns = list(outputs[0])
            conn.execute(
                text(
                    f"INSERT INTO {target_table} ({', '.join(columns)}) "
                    f"VALUES ({', '.join(':' + c for c in columns)})"
                ),
                outputs,
            )
        for row in rows:
            if row.get(key) in quarantined:
                conn.execute(
                    text(
                        f"INSERT INTO {quarantine_table} (source_key, invariant, payload) "
                        "VALUES (:k, :i, CAST(:p AS JSONB))"
                    ),
                    {"k": str(row.get(key)), "i": reasons.get(row.get(key), ""),
                     "p": json.dumps(row, default=str)},
                )

    return GuardedRunResult(
        report.action, len(outputs), len(quarantined), use_reference, report,
    )
