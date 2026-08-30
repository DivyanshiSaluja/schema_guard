"""Deployment, snapshot, and rollback utilities for approved candidates."""

from src.deploy.deployer import DeploymentResult, deploy_approved_candidate
from src.deploy.rollback import RollbackResult, rollback_latest
from src.deploy.snapshot_manager import SnapshotManager

__all__ = [
    "DeploymentResult",
    "RollbackResult",
    "SnapshotManager",
    "deploy_approved_candidate",
    "rollback_latest",
]
