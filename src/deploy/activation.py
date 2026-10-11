"""Atomic activation of a transformation module under a file lock."""

from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from src.deploy.snapshot_manager import DEFAULT_SNAPSHOT_DIR, DEFAULT_SOURCE_PATH, SnapshotManager


@contextmanager
def deployment_lock(target: Path):
    lock_path = target.with_suffix(target.suffix + ".lock")
    with open(lock_path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def activate_module(
    source: str,
    target: Path | str = DEFAULT_SOURCE_PATH,
    *,
    snapshot_dir: Path | str | None = None,
) -> Path | None:
    """Compile, snapshot the current module, then replace it atomically.

    Returns the snapshot path. Raises SyntaxError before touching the active
    module if ``source`` does not compile.
    """

    target = Path(target)
    compile(source, str(target), "exec")
    if snapshot_dir is None:
        snapshot_dir = (
            DEFAULT_SNAPSHOT_DIR if target.resolve() == DEFAULT_SOURCE_PATH.resolve()
            else target.parent / "_snapshots"
        )
    with deployment_lock(target):
        manager = SnapshotManager(target, snapshot_dir)
        snapshot = manager.snapshot_current_pipeline()
        fd, temp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(source)
        os.replace(temp_name, target)
    return snapshot
