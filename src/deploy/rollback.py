"""Restoration and ETL rerun after a failed deployment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.deploy.deployer import run_etl
from src.deploy.snapshot_manager import (
    DEFAULT_SOURCE_PATH,
    DEFAULT_SNAPSHOT_DIR,
    SnapshotManager,
)


@dataclass(frozen=True)
class RollbackResult:
    restored: bool
    etl_succeeded: bool
    rollback_succeeded: bool
    snapshot_path: Path | None
    error_log: str = ""


def rollback_latest(
    *,
    etl_runner: Callable[[], object] | None = None,
    source_path: Path | str = DEFAULT_SOURCE_PATH,
    snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
) -> RollbackResult:
    """Restore the latest valid snapshot and rerun the existing ETL."""

    manager = SnapshotManager(source_path, snapshot_dir)
    snapshot_path = manager.restore_latest_snapshot()
    if snapshot_path is None:
        return RollbackResult(
            restored=False,
            etl_succeeded=False,
            rollback_succeeded=False,
            snapshot_path=None,
            error_log="no valid snapshot available",
        )

    etl_succeeded, error_log = run_etl(etl_runner)
    return RollbackResult(
        restored=True,
        etl_succeeded=etl_succeeded,
        rollback_succeeded=etl_succeeded,
        snapshot_path=snapshot_path,
        error_log=error_log,
    )
