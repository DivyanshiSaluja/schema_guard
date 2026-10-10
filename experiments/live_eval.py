"""Laboratory validation against a live PostgreSQL 16 database.

Run from the repository root with Docker running:

    python -m experiments.live_eval

Starts an isolated container ``schemaguard-lab`` on port 55432 (the demo
database on 5432 is not touched), applies real ALTER TABLE changes, and runs
detection, verification, approval, deployment and the guarded ETL. Module
activation and plain ETL runs happen in a temporary copy of ``src/``.
Results are written to experiments/results/live_results.{json,md}.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import create_engine, text

from experiments.ddre_eval import baseline as existing_checks
from src.pipeline.guarded_etl import run_guarded_etl
from src.reference import guard
from src.reference.verify import verify_candidate
from src.sandbox.executor import DEFAULT_TEST_DATA


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments" / "results"
PORT = 55432
URL = f"postgresql://schemaguard:schemaguard@localhost:{PORT}/schemaguard"
CONTAINER = "schemaguard-lab"
SEED = (ROOT / "data" / "seed" / "01_init.sql").read_text()
INCUMBENT = (ROOT / "src" / "pipeline" / "transformations.py").read_text()
LLM_CANDIDATE = json.loads((ROOT / "data" / "candidates.json").read_text())[0]["code"]


def code(name_expr, email_expr="row['email']"):
    return f"def transform(row):\n    return {{'id': row['id'], 'name': {name_expr}, 'email': {email_expr}}}\n"


def start_database():
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run([
        "docker", "run", "-d", "--name", CONTAINER, "-p", f"{PORT}:5432",
        "-e", "POSTGRES_USER=schemaguard", "-e", "POSTGRES_PASSWORD=schemaguard",
        "-e", "POSTGRES_DB=schemaguard", "postgres:16",
    ], check=True, capture_output=True)
    engine = create_engine(URL)
    for _ in range(90):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except Exception:
            time.sleep(1)
    raise RuntimeError("database did not start")


def reset(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS warehouse CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS customers"))
        conn.exec_driver_sql(SEED)


def sql(engine, *statements):
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def scalar(engine, query):
    with engine.connect() as conn:
        return conn.execute(text(query)).scalar()


def make_workspace() -> Path:
    workspace = Path(tempfile.mkdtemp(prefix="schemaguard-lab-"))
    shutil.copytree(ROOT / "src", workspace / "src", ignore=shutil.ignore_patterns("__pycache__", "tests"))
    (workspace / "src" / "common" / "config.py").write_text(f'DB_URL = "{URL}"\n')
    return workspace


def plain_etl(workspace: Path) -> tuple[bool, str, float]:
    started = time.perf_counter()
    done = subprocess.run(
        [sys.executable, "-m", "src.pipeline.etl_pipeline"], cwd=workspace,
        capture_output=True, text=True,
    )
    elapsed = (time.perf_counter() - started) * 1000
    tail = (done.stdout or done.stderr).strip().splitlines()
    return done.returncode == 0, tail[-1] if tail else "", elapsed


def row_for(result, cid, note, live_rows, catalog, baseline_state, domain_note):
    domain = live_rows + guard.boundary_rows(catalog, live_rows)
    return {
        "candidate": cid, "note": note, "domain": domain_note,
        "committed_fixture": None,
        "existing_checks": "pass" if existing_checks(result[1], live_rows) else "reject",
        "verdict": result[0].verdict,
        "deployable": result[0].deployable,
        "needs_waiver": result[0].needs_waiver,
        "unverifiable": sorted(result[0].unverifiable_fields),
        "invariants": sorted(result[0].invariant_violations),
        "upstream": {k: list(v) for k, v in result[0].upstream_violations.items()},
        "witness_rows": len(result[0].witness.rows) if result[0].witness else 0,
        "witness_signature": list(result[0].witness.signature) if result[0].witness else [],
        "read_difference": {k: sorted(v) for k, v in result[0].witness.read_difference.items()} if result[0].witness else {},
        "domain_rows": result[0].domain_size,
    }


def verify_set(state, events, catalog, rows, candidates, invariants, include_boundary=True):
    started = time.perf_counter()
    results = guard.verify(state, events, catalog, rows, candidates, invariants, include_boundary=include_boundary)
    elapsed = (time.perf_counter() - started) * 1000 / max(len(candidates), 1)
    return list(zip(results, [c for _, c in candidates])), elapsed


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    engine = start_database()
    workspace = make_workspace()
    target_module = workspace / "src" / "pipeline" / "transformations.py"
    report: dict = {"postgres": scalar(engine, "SHOW server_version")}
    target_catalog_invariants = None

    try:
        # ---------------- L1: rename, verification -----------------
        reset(engine)
        state = guard.capture_baseline(engine, table="customers", incumbent_code=INCUMBENT)
        ok, msg, _ = plain_etl(workspace)
        report["pre_drift_etl"] = msg
        sql(engine, "ALTER TABLE customers RENAME COLUMN full_name TO name")
        events, catalog, rows = guard.detect(engine, state)
        target_catalog = guard.read_catalog(engine, "customers", "warehouse")
        invariants = guard.default_invariants(target_catalog, protected=("name", "email"))
        target_catalog_invariants = [i.name for i in invariants]
        candidates = [
            ("C1", code("row['name']")),
            ("C0", LLM_CANDIDATE),
            ("C2", code("row['email']")),
            ("C3", code("row['name'] if row['name'] != '' else None")),
            ("C4", code("row['name'] if len(row['name']) <= 40 else ''")),
        ]
        notes = {"C1": "correct", "C0": "generated by gpt-oss-120b in an earlier run (stale column)",
                 "C2": "reads the wrong column", "C3": "turns empty names into NULL",
                 "C4": "blanks names over 40 characters"}
        results, ms = verify_set(state, events, catalog, rows, candidates, invariants)
        seed_only, _ = verify_set(state, events, catalog, rows, candidates[3:], invariants, include_boundary=False)
        report["L1"] = {
            "events": guard.events_to_json(events),
            "invariants": target_catalog_invariants,
            "ms_per_candidate": round(ms, 1),
            "with_boundary": [row_for(r, cid, notes[cid], rows, catalog, state, "live + boundary") for (cid, _), r in zip(candidates, results)],
            "live_rows_only": [row_for(r, cid, notes[cid], rows, catalog, state, "live only") for (cid, _), r in zip(candidates[3:], seed_only)],
        }
        for item in report["L1"]["with_boundary"]:
            item["committed_fixture"] = "pass" if existing_checks(dict(candidates)[item["candidate"]], list(DEFAULT_TEST_DATA)) else "reject"

        # ---------------- L2: approve, deploy, upstream fault -----------------
        c1_result = results[0][0]
        domain = rows + guard.boundary_rows(catalog, rows)
        evidence = guard.approve(c1_result, candidates[0][1], state, events, catalog, invariants, domain, reviewer="lab-reviewer")
        deployed, reasons = guard.deploy(engine, evidence, candidates[0][1], state, target_module)
        ok, msg, plain_ms = plain_etl(workspace)
        report["L2"] = {"deployed": deployed, "reasons": list(reasons), "digest": evidence.digest,
                        "plain_etl_after_deploy": msg}

        sql(engine, "ALTER TABLE customers ALTER COLUMN name DROP NOT NULL",
            "UPDATE customers SET name = NULL WHERE id IN (11, 12, 13)")
        untrusted = guard.untrusted_invariants(state, invariants)
        started = time.perf_counter()
        run = run_guarded_etl(engine, candidate_code=candidates[0][1], incumbent_code=INCUMBENT,
                              events=events, invariants=invariants, untrusted_invariants=untrusted,
                              active_module=target_module)
        guarded_ms = (time.perf_counter() - started) * 1000
        report["L2"]["upstream_fault"] = {
            "action": run.action, "written": run.rows_written, "quarantined": run.rows_quarantined,
            "warehouse_rows": scalar(engine, "SELECT count(*) FROM warehouse.customers"),
            "warehouse_null_names": scalar(engine, "SELECT count(*) FROM warehouse.customers WHERE name IS NULL"),
            "quarantine_rows": scalar(engine, "SELECT count(*) FROM warehouse.customers_quarantine"),
            "guarded_etl_ms": round(guarded_ms, 1),
            "untrusted_invariants": sorted(untrusted),
        }
        ok, msg, _ = plain_etl(workspace)
        report["L2"]["unguarded_etl_same_data"] = {
            "result": msg,
            "warehouse_null_names": scalar(engine, "SELECT count(*) FROM warehouse.customers WHERE name IS NULL"),
        }

        # ---------------- L3: candidate-induced fault, reference fallback -----------------
        bug = candidates[4][1]
        guard.activate_module(bug, target_module)
        sql(engine, *[
            f"INSERT INTO customers (name, email) VALUES ('Alexandria Konstantinopoulou-Vanderbilt {'X' * i}', 'long{i}@example.com')"
            for i in range(1, 6)
        ])
        for fraction in (1.0, 0.2):
            sql(engine, "TRUNCATE warehouse.customers_quarantine")
            guard.activate_module(bug, target_module)
            started = time.perf_counter()
            run = run_guarded_etl(engine, candidate_code=bug, incumbent_code=INCUMBENT, events=events,
                                  invariants=invariants, untrusted_invariants=untrusted,
                                  sample_fraction=fraction, active_module=target_module)
            report.setdefault("L3", {})[f"sample_{fraction}"] = {
                "action": run.action, "reference_activated": run.reference_activated,
                "written": run.rows_written, "quarantined": run.rows_quarantined,
                "reference_rows_replayed": run.report.reference_rows,
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "per_invariant": [vars(i) | {"candidate_keys": len(i.candidate_keys), "reference_keys": len(i.reference_keys)} for i in run.report.per_invariant if i.attribution != "NO_FAULT"],
                "module_is_reference": target_module.read_text().startswith('"""Reference path'),
                "warehouse_blank_names": scalar(engine, "SELECT count(*) FROM warehouse.customers WHERE name = ''"),
            }
        guard.activate_module(render_reference(events), target_module)
        ok, msg, _ = plain_etl(workspace)
        report["L3"]["plain_etl_with_activated_reference"] = msg
        guard.activate_module(INCUMBENT, target_module)
        ok, msg, _ = plain_etl(workspace)
        report["L3"]["plain_etl_with_restored_pre_drift_snapshot"] = {"succeeded": ok, "error": msg}

        # ---------------- L4: second drift after approval -----------------
        reset(engine)
        state = guard.capture_baseline(engine, table="customers", incumbent_code=INCUMBENT)
        sql(engine, "ALTER TABLE customers RENAME COLUMN full_name TO name")
        events, catalog, rows = guard.detect(engine, state)
        result = guard.verify(state, events, catalog, rows, [("C1", candidates[0][1])], invariants)[0]
        evidence = guard.approve(result, candidates[0][1], state, events, catalog, invariants, rows, reviewer="lab-reviewer")
        sql(engine, "ALTER TABLE customers RENAME COLUMN email TO contact_email")
        deployed, reasons = guard.deploy(engine, evidence, candidates[0][1], state, target_module)
        report["L4"] = {"deployed": deployed, "reasons": list(reasons)}

        # ---------------- L5: split -----------------
        reset(engine)
        state = guard.capture_baseline(engine, table="customers", incumbent_code=INCUMBENT)
        sql(engine,
            "ALTER TABLE customers ADD COLUMN first_name TEXT, ADD COLUMN last_name TEXT",
            "UPDATE customers SET first_name = split_part(full_name, ' ', 1), last_name = split_part(full_name, ' ', 2)",
            "ALTER TABLE customers DROP COLUMN full_name")
        events, catalog, rows = guard.detect(engine, state)
        split_candidates = [("S1", code("row['first_name'] + ' ' + row['last_name']")), ("S2", code("row['first_name']"))]
        results, _ = verify_set(state, events, catalog, rows, split_candidates, invariants, include_boundary=False)
        report["L5"] = {"events": guard.events_to_json(events),
                        "results": [row_for(r, cid, "", rows, catalog, state, "live only") for (cid, _), r in zip(split_candidates, results)]}

        # ---------------- L6: merge -----------------
        reset(engine)
        state = guard.capture_baseline(engine, table="customers", incumbent_code=INCUMBENT)
        sql(engine,
            "ALTER TABLE customers ADD COLUMN contact TEXT",
            "UPDATE customers SET contact = full_name || '|' || email",
            "ALTER TABLE customers DROP COLUMN full_name, DROP COLUMN email",
            "INSERT INTO customers (contact) VALUES ('Pipe|Name|pipe@example.com')")
        events, catalog, rows = guard.detect(engine, state)
        merge_candidates = [
            ("M1", code("row['contact'].split('|')[0]", "row['contact'].split('|')[1]")),
            ("M2", code("row['contact'].split('|')[0]", "row['contact']")),
        ]
        results, _ = verify_set(state, events, catalog, rows, merge_candidates, invariants, include_boundary=False)
        report["L6"] = {"events": guard.events_to_json(events),
                        "results": [row_for(r, cid, "", rows, catalog, state, "live only") for (cid, _), r in zip(merge_candidates, results)]}
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        shutil.rmtree(workspace, ignore_errors=True)

    (OUT / "live_results.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


def render_reference(events):
    from src.reference.artifact import render_reference_module
    return render_reference_module(INCUMBENT, events)


if __name__ == "__main__":
    main()
