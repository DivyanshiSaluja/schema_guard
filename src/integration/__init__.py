"""Orchestration for the SchemaGuard MVP validation and deployment flow."""

from src.integration.pipeline import IntegrationResult, run_integration

__all__ = ["IntegrationResult", "run_integration"]
