"""Measure drift-derived reference execution against the existing checks.

Run from the repository root:

    python -m experiments.ddre_eval

Rows mirror data/seed/01_init.sql (ids 1..500, "Customer <n>",
"customer<n>@example.com") plus four boundary rows. No database is needed.
Results are written to experiments/results/.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.common.models import SandboxResult
from src.reference.attribution import attribute_batch, incumbent_health
from src.reference.drift import normalize_drift
from src.reference.evidence import build_evidence, check_freshness
from src.reference.invariants import NON_EMPTY, NOT_NULL, UNIQUE, Invariant
from src.reference.verify import run_candidate, verify_candidate
from src.sandbox.executor import DEFAULT_TEST_DATA, _compile_transform
from src.validator.data_quality import validate_data_quality


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments" / "results"
INCUMBENT = (ROOT / "src" / "pipeline" / "transformations.py").read_text()

INVARIANTS = (
    Invariant(UNIQUE, "id"),
    Invariant(NOT_NULL, "id"),
    Invariant(NON_EMPTY, "name"),
    Invariant(NOT_NULL, "email"),
)

PRIOR = {"id": "integer", "full_name": "text", "email": "text"}


def code(name_expr: str, id_expr: str = "row['id']", email_expr: str = "row['email']", extra: str = "") -> str:
    return (
        "def transform(row):\n"
        f"    return {{'id': {id_expr}, 'name': {name_expr}, 'email': {email_expr}{extra}}}\n"
    )


def seed_rows():
    return [
        {"id": n, "full_name": f"Customer {n}", "email": f"customer{n}@example.com"}
        for n in range(1, 501)
    ]


def boundary_rows():
    return [
        {"id": 501, "full_name": "Zoë Ålvarez-Nuñez", "email": "zoe@example.com"},
        {"id": 502, "full_name": "Bartholomew Maximilian Fitzgerald-Worthington III", "email": "bart@example.com"},
        {"id": 503, "full_name": "Siobhán O'Connor", "email": "siobhan@example.com"},
        {"id": 504, "full_name": "", "email": "blank@example.com"},
    ]


def rename(rows, old, new):
    return [{(new if k == old else k): v for k, v in row.items()} for row in rows]


def baseline(candidate_code, rows):
    """The repo's existing checks: restricted execution plus data_quality."""

    try:
        transform = _compile_transform(candidate_code)
    except Exception:
        return False
    outputs, ok = [], True
    for row in rows:
        out, _, _ = run_candidate(transform, row)
        if out is None:
            ok = False
            break
        outputs.append(out)
    result = SandboxResult("b", ok, len(rows), len(outputs) if ok else 0, 0.0)
    return validate_data_quality(outputs, result).data_quality_ok


def verdict_cell(result):
    if result.deployable:
        return "EQUIVALENT: deployable"
    if result.needs_waiver:
        return f"UNVERIFIABLE {sorted(result.unverifiable_fields)}: waiver needed"
    parts = [result.verdict]
    if result.invariant_violations:
        parts.append("invariant " + ", ".join(sorted(result.invariant_violations)))
    return "; ".join(parts) + ": rejected"


def upstream_cell(result):
    return "; ".join(f"{name} on keys {list(keys)}" for name, keys in result.upstream_violations.items())


def witness_cell(result):
    w = result.witness
    if w is None:
        return ""
    diff = w.read_difference
    reads = ""
    if diff["candidate_only"] or diff["reference_only"]:
        reads = f"; read only by candidate {sorted(diff['candidate_only'])}, only by reference {sorted(diff['reference_only'])}"
    return f"{len(w.rows)} row(s), key {[r.get('id') for r in w.rows]}, {w.signature}{reads}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pre = seed_rows() + boundary_rows()
    probe = pre[9:500:10]  # 50 retained pre-drift rows
    records = []

    def run(scenario, events, candidates, domain, committed_fixture=None):
        for cid, ccode, note in candidates:
            started = time.perf_counter()
            result = verify_candidate(cid, ccode, INCUMBENT, events, domain, INVARIANTS)
            elapsed = (time.perf_counter() - started) * 1000
            records.append({
                "scenario": scenario,
                "candidate": cid,
                "note": note,
                "committed_baseline": None if committed_fixture is None else (
                    "pass" if baseline(ccode, committed_fixture) else "reject"),
                "baseline_on_changed_rows": "pass" if baseline(ccode, domain) else "reject",
                "ddre": verdict_cell(result),
                "witness": witness_cell(result),
                "upstream_in_domain": upstream_cell(result),
                "domain_rows": len(domain),
                "divergent_cells": len(result.divergences),
                "ms": round(elapsed, 1),
            })

    # A. Rename full_name -> name, confirmed on the probe set.
    post = rename(pre, "full_name", "name")
    rename_schema = {"id": "integer", "name": "text", "email": "text"}
    ev_rename = normalize_drift("customers", PRIOR, rename_schema, probe_rows=probe, live_rows=post)
    rename_candidates = [
        ("C1", code("row['name']"), "correct"),
        ("C0", code("row['full_name']"), "stale: the committed repair"),
        ("C2", code("row['email']"), "reads the wrong column"),
        ("C3", code("row['name'] if row['name'] != '' else None"), "turns empty names into NULL"),
        ("C4", code("row['name'] if len(row['name']) <= 40 else ''"), "blanks names over 40 chars"),
    ]
    run("A. Rename", ev_rename, rename_candidates, post, committed_fixture=list(DEFAULT_TEST_DATA))
    run("A'. Rename, seed rows only", ev_rename, rename_candidates[3:], post[:500])

    # B. Added column phone.
    post_b = [dict(row, phone=f"+91-90000{row['id']:05d}") for row in pre]
    ev_add = normalize_drift("customers", PRIOR, dict(PRIOR, phone="text"), probe_rows=probe, live_rows=post_b)
    run("B. Add column", ev_add, [
        ("A1", INCUMBENT, "incumbent unchanged"),
        ("A2", code("row['full_name']", email_expr="row['phone']"), "writes phone into email"),
        ("A3", code("row['full_name']", extra=", 'phone': row['phone']"), "adds an output field"),
    ], post_b)

    # C. Retype id integer -> text.
    post_c = [dict(row, id=str(row["id"])) for row in pre]
    ev_retype = normalize_drift("customers", PRIOR, dict(PRIOR, id="text"), probe_rows=probe, live_rows=post_c)
    run("C. Retype id", ev_retype, [
        ("R1", code("row['full_name']", id_expr="int(row['id'])"), "casts back to integer"),
        ("R2", INCUMBENT, "keeps text id"),
    ], post_c)

    # D. Dropped column email.
    post_d = [{k: v for k, v in row.items() if k != "email"} for row in pre]
    ev_drop = normalize_drift("customers", PRIOR, {"id": "integer", "full_name": "text"}, probe_rows=probe, live_rows=post_d)
    run("D. Drop column", ev_drop, [
        ("D1", code("row['full_name']", email_expr="None"), "writes NULL email"),
        ("D2", code("row['full_name']", email_expr="'unknown@invalid.example'"), "writes a placeholder email"),
    ], post_d)

    # E. Ambiguous rename: two new columns both carry the old values.
    post_e = [dict(row, display_name=row["name"]) for row in post]
    ev_amb = normalize_drift("customers", PRIOR, dict(rename_schema, display_name="text"), probe_rows=probe, live_rows=post_e)
    run("E. Ambiguous rename", ev_amb, [("C1", code("row['name']"), "correct if name is the rename")], post_e)

    # F. Post-deployment attribution.
    attributions = []
    batch_upstream = [dict(row) for row in post[:200]]
    for row in batch_upstream[10:13]:
        row["name"] = None
    rep = attribute_batch(batch_upstream, code("row['name']"), INCUMBENT, ev_rename, INVARIANTS)
    attributions.append(("C1 deployed; 3 upstream rows arrive with NULL name", rep))

    batch_long = [dict(row) for row in post[:195]] + [
        {"id": 900 + i, "name": "Alexandria Konstantinopoulou-Vanderbilt " + "X" * i, "email": f"long{i}@example.com"}
        for i in range(1, 6)
    ]
    rep = attribute_batch(batch_long, code("row['name'] if len(row['name']) <= 40 else ''"), INCUMBENT, ev_rename, INVARIANTS)
    attributions.append(("C4 deployed; 5 rows with names over 40 chars", rep))

    buggy_incumbent = "def transform(row):\n    return {'id': row['id'], 'name': None if row['id'] % 100 == 0 else row['full_name'], 'email': row['email']}\n"
    untrusted = incumbent_health(buggy_incumbent, probe, INVARIANTS)
    rep = attribute_batch(batch_upstream, code("row['name']"), buggy_incumbent, ev_rename, INVARIANTS, untrusted_invariants=untrusted)
    attributions.append((f"Buggy incumbent (health check flags {sorted(untrusted)}); same upstream batch", rep))

    snapshot = _compile_transform(INCUMBENT)
    snapshot_failures = sum(1 for row in batch_long if run_candidate(snapshot, row)[0] is None)

    # G. Evidence digest and freshness.
    approved = code("row['name']")
    evidence = build_evidence(
        candidate_code=approved, incumbent_code=INCUMBENT, events=ev_rename,
        prior_schema=PRIOR, changed_schema=rename_schema,
        invariant_names=[i.name for i in INVARIANTS], domain=post,
        verdict="EQUIVALENT", approval={"reviewer": "reviewer-1", "decision": "approve"},
    )
    reformatted = "def transform(row):\n    return {\n        'id': row['id'],\n        'name': row['name'],\n        'email': row['email'],\n    }\n"
    freshness = [
        ("Same code, reformatted", check_freshness(evidence, reformatted, rename_schema)),
        ("Different code (C2) written instead", check_freshness(evidence, code("row['email']"), rename_schema)),
        ("Second drift after approval (email renamed)", check_freshness(evidence, approved, {"id": "integer", "name": "text", "contact_email": "text"})),
    ]

    write_reports(records, ev_rename, ev_amb, attributions, snapshot_failures, len(batch_long), freshness, evidence)


def write_reports(records, ev_rename, ev_amb, attributions, snapshot_failures, batch_size, freshness, evidence):
    (OUT / "ddre_results.json").write_text(json.dumps({
        "verification": records,
        "attribution": [
            {"case": case, "action": rep.action, "quarantined": list(rep.quarantined_keys),
             "per_invariant": [vars(item) for item in rep.per_invariant]}
            for case, rep in attributions
        ],
        "snapshot_rollback_failures": [snapshot_failures, batch_size],
        "freshness": [{"case": c, "ok": ok, "reasons": list(r)} for c, (ok, r) in freshness],
        "evidence_digest": evidence.digest,
    }, indent=2, default=str))

    lines = ["# DDRE measured results", ""]
    lines.append(f"Rename confirmation: {ev_rename[0].drift_class} {ev_rename[0].old_column} -> {ev_rename[0].new_column}, match ratio {ev_rename[0].match_ratio:.2f} on 50 probe rows.")
    lines.append(f"Ambiguity case: {ev_amb[0].drift_class} for {ev_amb[0].old_column}, candidates {list(ev_amb[0].candidates)}.")
    lines += ["", "| Scenario | Candidate | Defect | Committed fixture | Existing checks on changed rows | DDRE verdict | Witness | Shared with reference (input data) | ms |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in records:
        lines.append(f"| {r['scenario']} ({r['domain_rows']} rows) | {r['candidate']} | {r['note']} | {r['committed_baseline'] or '-'} | {r['baseline_on_changed_rows']} | {r['ddre']} | {r['witness']} | {r['upstream_in_domain']} | {r['ms']} |")
    lines += ["", "| Post-deployment case | Action | Quarantined keys | Detail |", "| --- | --- | --- | --- |"]
    for case, rep in attributions:
        detail = "; ".join(f"{i.invariant}: {i.attribution}" for i in rep.per_invariant if i.attribution != "NO_FAULT")
        lines.append(f"| {case} | {rep.action} | {list(rep.quarantined_keys)} | {detail} |")
    lines.append(f"\nRestoring the pre-drift snapshot on the C4 batch: {snapshot_failures} of {batch_size} rows fail.")
    lines += ["", "| Freshness check | Deploy allowed | Reason |", "| --- | --- | --- |"]
    for case, (ok, reasons) in freshness:
        lines.append(f"| {case} | {'yes' if ok else 'no'} | {'; '.join(reasons)} |")
    (OUT / "ddre_results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
