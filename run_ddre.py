"""Command-line flow for drift-derived reference execution.

    python run_ddre.py baseline                     # before any change
    python run_ddre.py detect                       # after the source changes
    python run_ddre.py verify --candidates FILE     # JSON list of {"id", "code"}
    python run_ddre.py verify --generate MODEL      # LLM loop (needs NVIDIA_API_KEY)
    python run_ddre.py approve --candidate ID --reviewer NAME [--waive name,email]
    python run_ddre.py deploy
    python run_ddre.py etl [--sample 0.2]

State is kept in data/ddre_state.json between commands.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine

from src.common.config import DB_URL
from src.deploy.snapshot_manager import DEFAULT_SOURCE_PATH
from src.pipeline.guarded_etl import run_guarded_etl
from src.reference import guard
from src.reference.evidence import EvidenceRecord
from src.reference.invariants import Invariant


def main() -> int:
    parser = argparse.ArgumentParser(description="Drift-derived reference execution")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("baseline")
    sub.add_parser("detect")
    verify = sub.add_parser("verify")
    verify.add_argument("--candidates")
    verify.add_argument("--generate", metavar="MODEL")
    approve = sub.add_parser("approve")
    approve.add_argument("--candidate", required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--waive", default="")
    sub.add_parser("deploy")
    etl = sub.add_parser("etl")
    etl.add_argument("--sample", type=float, default=1.0)
    args = parser.parse_args()

    engine = create_engine(DB_URL)
    state = guard.load_state()

    if args.command == "baseline":
        state = {"baseline": guard.capture_baseline(
            engine, table="customers", incumbent_code=Path(DEFAULT_SOURCE_PATH).read_text())}
        print(f"Baseline saved: {len(state['baseline']['probe'])} probe rows")
    elif args.command == "detect":
        events, catalog, rows = guard.detect(engine, state["baseline"])
        state["events"] = guard.events_to_json(events)
        for event in events:
            print(event)
    elif args.command == "verify":
        baseline = state["baseline"]
        events, catalog, rows = guard.detect(engine, baseline)
        invariants = guard.default_invariants(
            guard.read_catalog(engine, "customers", "warehouse"), protected=("name", "email"))
        if args.generate:
            from src.agent.ddre_agent import repair
            domain = rows + guard.boundary_rows(catalog, rows)
            from src.reference.verify import verify_candidate
            history = repair(events, baseline["incumbent_code"],
                             lambda code: verify_candidate("llm", code, baseline["incumbent_code"], events, domain, invariants),
                             model=args.generate)
            candidates = [(f"{args.generate}-{i + 1}", code) for i, (code, _) in enumerate(history)]
        else:
            candidates = [(c["id"], c["code"]) for c in json.loads(Path(args.candidates).read_text())]
        results = guard.verify(baseline, events, catalog, rows, candidates, invariants)
        state["events"] = guard.events_to_json(events)
        state["invariants"] = [asdict(i) for i in invariants]
        state["candidates"] = dict(candidates)
        state["results"] = {r.candidate_id: {
            "verdict": r.verdict, "deployable": r.deployable, "needs_waiver": r.needs_waiver,
            "unverifiable": sorted(r.unverifiable_fields),
            "invariants": sorted(r.invariant_violations),
            "witness": list(r.witness.rows) if r.witness else [],
        } for r in results}
        for r in results:
            print(f"{r.candidate_id}: {r.verdict} deployable={r.deployable} "
                  f"waiver={r.needs_waiver} invariants={sorted(r.invariant_violations)}")
    elif args.command == "approve":
        baseline = state["baseline"]
        events, catalog, rows = guard.detect(engine, baseline)
        invariants = tuple(Invariant(**i) for i in state["invariants"])
        code = state["candidates"][args.candidate]
        result = guard.verify(baseline, events, catalog, rows, [(args.candidate, code)], invariants)[0]
        domain = rows + guard.boundary_rows(catalog, rows)
        waived = tuple(f for f in args.waive.split(",") if f)
        evidence = guard.approve(result, code, baseline, events, catalog, invariants, domain,
                                 reviewer=args.reviewer, waived_fields=waived)
        state["approved"] = {"candidate": args.candidate, "evidence": asdict(evidence)}
        print(f"Approved {args.candidate}; evidence digest {evidence.digest}")
    elif args.command == "deploy":
        approved = state["approved"]
        evidence = EvidenceRecord(**{k: tuple(map(tuple, v)) if k == "approval" else v
                                     for k, v in approved["evidence"].items()})
        ok, reasons = guard.deploy(engine, evidence, state["candidates"][approved["candidate"]],
                                   state["baseline"], DEFAULT_SOURCE_PATH)
        print("Deployed" if ok else f"Refused: {'; '.join(reasons)}")
    elif args.command == "etl":
        baseline = state["baseline"]
        events = guard.events_from_json(state["events"])
        invariants = tuple(Invariant(**i) for i in state["invariants"])
        run = run_guarded_etl(
            engine, candidate_code=Path(DEFAULT_SOURCE_PATH).read_text(),
            incumbent_code=baseline["incumbent_code"], events=events, invariants=invariants,
            untrusted_invariants=guard.untrusted_invariants(baseline, invariants),
            sample_fraction=args.sample, active_module=DEFAULT_SOURCE_PATH,
        )
        print(f"{run.action}: wrote {run.rows_written}, quarantined {run.rows_quarantined}, "
              f"reference activated {run.reference_activated}")
    guard.save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
