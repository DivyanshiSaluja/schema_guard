"""Execute repair candidates against controlled data.

The sandbox deliberately does not create a database connection or expose any
application objects to candidate code. Candidates are limited to a small,
data-transformation-oriented Python subset before they are compiled.
"""

from __future__ import annotations

import ast
import copy
import time
import traceback
from collections.abc import Mapping, Sequence
from typing import Any

from src.common.models import RepairCandidate, SandboxResult


DEFAULT_TEST_DATA = (
    {"id": 1, "full_name": "Ada Lovelace", "email": "ada@example.com"},
    {"id": 2, "full_name": "Grace Hopper", "email": "grace@example.com"},
)

_SAFE_BUILTINS = {"bool": bool, "float": float, "int": int, "len": len, "str": str}
_ALLOWED_CALLS = frozenset(_SAFE_BUILTINS)
_ALLOWED_NODES = (
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.Assign,
    ast.AnnAssign,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Dict,
    ast.List,
    ast.Tuple,
    ast.Subscript,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    ast.keyword,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


class UnsafeCandidateError(ValueError):
    """Raised when candidate source is outside the supported safe subset."""


class _CandidateValidator(ast.NodeVisitor):
    def generic_visit(self, node: ast.AST) -> None:
        if not isinstance(node, _ALLOWED_NODES):
            raise UnsafeCandidateError(
                f"unsupported syntax: {type(node).__name__}"
            )
        super().generic_visit(node)

    def visit_Module(self, node: ast.Module) -> None:
        if len(node.body) != 1 or not isinstance(node.body[0], ast.FunctionDef):
            raise UnsafeCandidateError("candidate must define exactly one transform function")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name != "transform":
            raise UnsafeCandidateError("candidate function must be named transform")
        if node.decorator_list:
            raise UnsafeCandidateError("decorators are not allowed")
        if len(node.args.args) != 1 or node.args.vararg or node.args.kwarg:
            raise UnsafeCandidateError("transform must accept exactly one argument")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise UnsafeCandidateError("dunder names are not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS:
            raise UnsafeCandidateError("only approved pure helper functions may be called")
        self.generic_visit(node)


def _compile_transform(code: str):
    tree = ast.parse(code, mode="exec")
    _CandidateValidator().visit(tree)
    namespace: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
    exec(compile(tree, "<repair-candidate>", "exec"), namespace, namespace)
    return namespace["transform"]


class SandboxExecutor:
    """Run a candidate over copied, in-memory rows and return its metrics."""

    def __init__(self, test_data: Sequence[Mapping[str, Any]] | None = None) -> None:
        self.test_data = tuple(test_data or DEFAULT_TEST_DATA)

    def execute(self, candidate: RepairCandidate) -> SandboxResult:
        before = len(self.test_data)
        after = 0
        error_log = ""
        started = time.perf_counter()

        try:
            transform = _compile_transform(candidate.code)
            transformed_rows = []
            for row in self.test_data:
                transformed = transform(copy.deepcopy(dict(row)))
                if not isinstance(transformed, Mapping):
                    raise TypeError("transform must return a mapping for every row")
                transformed_rows.append(transformed)
            after = len(transformed_rows)
            success = True
        except Exception:
            error_log = traceback.format_exc()
            success = False

        elapsed_ms = (time.perf_counter() - started) * 1000
        candidate.passed_sandbox = success
        candidate.sandbox_log = error_log
        return SandboxResult(
            candidate_id=candidate.id,
            ran_successfully=success,
            row_count_before=before,
            row_count_after=after,
            execution_time_ms=elapsed_ms,
            error_log=error_log,
        )


def execute_candidate(
    candidate: RepairCandidate,
    test_data: Sequence[Mapping[str, Any]] | None = None,
) -> SandboxResult:
    """Convenience wrapper for one sandbox execution."""

    return SandboxExecutor(test_data).execute(candidate)
