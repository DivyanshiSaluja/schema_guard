"""Evidence digest that binds an approval to the exact artifacts it covered."""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from src.reference.drift import DriftEvent


VALIDATOR_VERSION = "ddre-0.1"


def _sha(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def code_hash(code: str) -> str:
    """Hash of the normalized syntax tree, insensitive to formatting."""

    return _sha(ast.dump(ast.parse(code), annotate_fields=True))


def schema_fingerprint(schema: Mapping[str, str]) -> str:
    return _sha(dict(schema))


@dataclass(frozen=True)
class EvidenceRecord:
    candidate_hash: str
    drift_hash: str
    prior_schema: str
    changed_schema: str
    reference_hash: str
    invariant_hash: str
    domain_hash: str
    verdict: str
    validator_version: str
    approval: tuple[tuple[str, str], ...]
    digest: str


def build_evidence(
    *,
    candidate_code: str,
    incumbent_code: str,
    events: Sequence[DriftEvent],
    prior_schema: Mapping[str, str],
    changed_schema: Mapping[str, str],
    invariant_names: Sequence[str],
    domain: Sequence[Mapping[str, Any]],
    verdict: str,
    approval: Mapping[str, str],
) -> EvidenceRecord:
    parts = {
        "candidate_hash": code_hash(candidate_code),
        "drift_hash": _sha([asdict(event) for event in events]),
        "prior_schema": schema_fingerprint(prior_schema),
        "changed_schema": schema_fingerprint(changed_schema),
        "reference_hash": _sha(
            [code_hash(incumbent_code), [asdict(event) for event in events]]
        ),
        "invariant_hash": _sha(sorted(invariant_names)),
        "domain_hash": _sha(list(domain)),
        "verdict": verdict,
        "validator_version": VALIDATOR_VERSION,
        "approval": tuple(sorted(approval.items())),
    }
    return EvidenceRecord(**parts, digest=_sha(parts))


def check_freshness(
    record: EvidenceRecord,
    artifact_code: str,
    live_schema: Mapping[str, str],
) -> tuple[bool, tuple[str, ...]]:
    """Recompute what can change after approval; any mismatch voids it."""

    reasons = []
    if code_hash(artifact_code) != record.candidate_hash:
        reasons.append("artifact differs from the approved candidate")
    if schema_fingerprint(live_schema) != record.changed_schema:
        reasons.append("live source schema changed after approval")
    return not reasons, tuple(reasons)
