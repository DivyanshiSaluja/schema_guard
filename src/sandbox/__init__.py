"""Safe, in-memory execution of repair candidates."""

from src.sandbox.executor import SandboxExecutor, execute_candidate

__all__ = ["SandboxExecutor", "execute_candidate"]
