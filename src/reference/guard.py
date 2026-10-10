"""Orchestrates drift-derived reference execution against a live database.

Steps, each persisted in ``data/ddre_state.json`` so they can run as separate
commands:

1. ``capture_baseline``  - catalog of the source table, probe rows, incumbent code.
2. ``detect``            - live catalog and rows, normalized drift events.
3. ``verify``            - every candidate against the reference path on a
                           domain of live rows plus catalog-derived boundary rows.
4. ``approve``           - evidence digest over the verified artifacts.
5. ``deploy``            - freshness check, then atomic activation.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import text

from src.deploy.activation import activate_module
from src.monitor.probe_store import select_probe
from src.reference.attribution import incumbent_health
from src.reference.drift import DriftEvent, normalize_drift
from src.reference.evidence import EvidenceRecord, build_evidence, check_freshness
from src.reference.invariants import NON_EMPTY, NOT_NULL, UNIQUE, Invariant
from src.reference.verify import VerificationResult, verify_candidate


STATE_PATH = Path("data/ddre_state.json")
TEXT_TYPES = {"text", "character varying", "character"}
INT_TYPES = {"integer", "bigint", "smallint"}


def read_catalog(engine, table: str, schema: str = "public") -> dict[str, dict[str, Any]]:
    """Column -> {type, nullable}, restricted to one schema."""

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t ORDER BY ordinal_position"
        ), {"s": schema, "t": table}).all()
    return {name: {"type": dtype, "nullable": nullable == "YES"} for name, dtype, nullable in rows}


def read_rows(engine, table: str, schema: str = "public") -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(f"SELECT * FROM {schema}.{table}")).mappings()]


def types_of(catalog: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {name: info["type"] for name, info in catalog.items()}


def boundary_rows(catalog: dict[str, dict[str, Any]], rows: list[dict[str, Any]], key: str = "id") -> list[dict[str, Any]]:
    """Rows exercising catalog limits: empty, long, non-ASCII, quoted, NULL."""

    if not rows:
        return []
    base = rows[0]
    next_key = max(int(r[key]) for r in rows if str(r[key]).lstrip("-").isdigit()) + 1
    variants: list[dict[str, Any]] = []
    for name, info in catalog.items():
        if name == key:
            continue
        values: list[Any] = []
        if info["type"] in TEXT_TYPES:
            values = ["", "Zoë Ålvarez-Nuñez", "Siobhán O'Connor", "W" * 64]
        elif info["type"] in INT_TYPES:
            values = [0, -1, 2**31 - 1]
        if info["nullable"]:
            values.append(None)
        for value in values:
            row = dict(base, **{name: value})
            key_type = catalog.get(key, {}).get("type")
            row[key] = str(next_key) if key_type in TEXT_TYPES else next_key
            next_key += 1
            variants.append(row)
    return variants


def default_invariants(target_catalog: dict[str, dict[str, Any]], key: str = "id", protected: tuple[str, ...] = ()) -> tuple[Invariant, ...]:
    """Key uniqueness and not-null from the catalog, plus protected fields."""

    found = [Invariant(UNIQUE, key), Invariant(NOT_NULL, key)]
    for name, info in target_catalog.items():
        if name != key and not info["nullable"]:
            found.append(Invariant(NOT_NULL, name))
    found += [Invariant(NON_EMPTY, name) for name in protected]
    return tuple(found)


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, default=str))


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def capture_baseline(engine, *, table: str, incumbent_code: str, key: str = "id") -> dict[str, Any]:
    catalog = read_catalog(engine, table)
    rows = read_rows(engine, table)
    return {
        "table": table,
        "key": key,
        "catalog": catalog,
        "probe": select_probe(rows, key=key),
        "incumbent_code": incumbent_code,
    }


def detect(engine, baseline: dict[str, Any]) -> tuple[tuple[DriftEvent, ...], dict, list]:
    catalog = read_catalog(engine, baseline["table"])
    rows = read_rows(engine, baseline["table"])
    events = normalize_drift(
        baseline["table"], types_of(baseline["catalog"]), types_of(catalog),
        probe_rows=baseline["probe"], live_rows=rows, key=baseline["key"],
    )
    return events, catalog, rows


def verify(
    baseline: dict[str, Any],
    events: tuple[DriftEvent, ...],
    catalog: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    candidates: list[tuple[str, str]],
    invariants: tuple[Invariant, ...],
    *,
    include_boundary: bool = True,
) -> list[VerificationResult]:
    domain = rows + (boundary_rows(catalog, rows, baseline["key"]) if include_boundary else [])
    return [
        verify_candidate(cid, code, baseline["incumbent_code"], events, domain, invariants, key=baseline["key"])
        for cid, code in candidates
    ]


def untrusted_invariants(baseline: dict[str, Any], invariants: tuple[Invariant, ...]) -> frozenset[str]:
    return incumbent_health(baseline["incumbent_code"], baseline["probe"], invariants, key=baseline["key"])


def approve(
    result: VerificationResult,
    candidate_code: str,
    baseline: dict[str, Any],
    events: tuple[DriftEvent, ...],
    catalog: dict[str, dict[str, Any]],
    invariants: tuple[Invariant, ...],
    domain_rows: list[dict[str, Any]],
    *,
    reviewer: str,
    waived_fields: tuple[str, ...] = (),
) -> EvidenceRecord:
    """Bind a human approval to the verified artifacts; refuse what may not pass."""

    if result.invariant_violations:
        raise ValueError(f"hard invariants violated: {sorted(result.invariant_violations)}")
    if not result.deployable:
        missing = set(result.unverifiable_fields) - set(waived_fields)
        if not result.needs_waiver or missing:
            raise ValueError(
                f"verdict {result.verdict}; unwaived unverifiable fields: {sorted(missing)}"
            )
    return build_evidence(
        candidate_code=candidate_code,
        incumbent_code=baseline["incumbent_code"],
        events=events,
        prior_schema=types_of(baseline["catalog"]),
        changed_schema=types_of(catalog),
        invariant_names=[inv.name for inv in invariants],
        domain=domain_rows,
        verdict=result.verdict,
        approval={"reviewer": reviewer, "waived": ",".join(sorted(waived_fields))},
    )


def deploy(engine, evidence: EvidenceRecord, artifact_code: str, baseline: dict[str, Any], target: Path | str) -> tuple[bool, tuple[str, ...]]:
    """Recompute what may have changed since approval, then activate."""

    live = types_of(read_catalog(engine, baseline["table"]))
    ok, reasons = check_freshness(evidence, artifact_code, live)
    if ok:
        activate_module(artifact_code, target)
    return ok, reasons


def events_to_json(events: tuple[DriftEvent, ...]) -> list[dict[str, Any]]:
    return [asdict(event) for event in events]


def events_from_json(items: list[dict[str, Any]]) -> tuple[DriftEvent, ...]:
    return tuple(
        DriftEvent(**{k: tuple(v) if isinstance(v, list) else v for k, v in item.items()})
        for item in items
    )
