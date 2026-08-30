"""Timestamped snapshots of the pipeline transformation module."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_PATH = PROJECT_ROOT / "src" / "pipeline" / "transformations.py"
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "data" / "schema_snapshots"


class SnapshotManager:
    """Create and restore validated transformation snapshots."""

    def __init__(
        self,
        source_path: Path | str = DEFAULT_SOURCE_PATH,
        snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
    ) -> None:
        self.source_path = Path(source_path)
        self.snapshot_dir = Path(snapshot_dir)

    def snapshot_current_pipeline(self) -> Path | None:
        """Save the current source using a unique timestamped filename."""

        if not self.source_path.is_file():
            return None
        try:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            snapshot_path = self.snapshot_dir / f"transformations_{timestamp}.py"
            while snapshot_path.exists():
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                snapshot_path = self.snapshot_dir / f"transformations_{timestamp}.py"
            shutil.copy2(self.source_path, snapshot_path)
            return snapshot_path
        except OSError:
            return None

    def find_latest_snapshot(self) -> Path | None:
        """Return the newest non-empty, syntactically valid snapshot."""

        if not self.snapshot_dir.is_dir():
            return None
        candidates = sorted(
            self.snapshot_dir.glob("transformations_*.py"),
            key=lambda path: path.name,
            reverse=True,
        )
        for candidate in candidates:
            if self._is_valid_snapshot(candidate):
                return candidate
        return None

    def restore_snapshot(self, snapshot_path: Path | str) -> Path | None:
        """Restore a valid snapshot, refusing paths outside snapshot_dir."""

        path = Path(snapshot_path)
        try:
            snapshot_root = self.snapshot_dir.resolve()
            resolved_path = path.resolve()
            resolved_path.relative_to(snapshot_root)
            if not self._is_valid_snapshot(resolved_path):
                return None
            self.source_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved_path, self.source_path)
            return resolved_path
        except (OSError, ValueError):
            return None

    def restore_latest_snapshot(self) -> Path | None:
        """Restore the latest valid snapshot without deleting it."""

        latest = self.find_latest_snapshot()
        return self.restore_snapshot(latest) if latest is not None else None

    @staticmethod
    def _is_valid_snapshot(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            return True
        except (OSError, SyntaxError, UnicodeError):
            return False


def snapshot_current_pipeline(
    source_path: Path | str = DEFAULT_SOURCE_PATH,
    snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
) -> Path | None:
    return SnapshotManager(source_path, snapshot_dir).snapshot_current_pipeline()


def find_latest_snapshot(
    snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
    source_path: Path | str = DEFAULT_SOURCE_PATH,
) -> Path | None:
    return SnapshotManager(source_path, snapshot_dir).find_latest_snapshot()


def restore_latest_snapshot(
    source_path: Path | str = DEFAULT_SOURCE_PATH,
    snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
) -> Path | None:
    return SnapshotManager(source_path, snapshot_dir).restore_latest_snapshot()
