"""Retained pre-change probe records used to confirm drift correspondences.

The probe set is a deterministic, hash-selected sample of source rows taken
while the schema is still the baseline. After a change it is joined by key to
the live rows to confirm renames, splits and merges, and to run the incumbent
health check.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.reference.drift import required_probe_size


PROBE_PATH = Path("data/probe_snapshot.json")


def select_probe(rows: list[dict[str, Any]], *, key: str = "id", size: int | None = None) -> list[dict[str, Any]]:
    """Pick ``size`` rows by key hash so the same keys are chosen every time."""

    size = size or 2 * required_probe_size()
    ranked = sorted(
        rows, key=lambda row: hashlib.sha256(str(row[key]).encode()).hexdigest()
    )
    return ranked[:size]


def save_probe(rows: list[dict[str, Any]], path: Path | str = PROBE_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rows, indent=2, default=str))


def load_probe(path: Path | str = PROBE_PATH) -> list[dict[str, Any]]:
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else []
