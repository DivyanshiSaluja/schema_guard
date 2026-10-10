"""Source fields read by a transformation, captured dynamically and statically."""

from __future__ import annotations

import ast
from typing import Any


class RecordingRow(dict):
    """A row that records every key the transformation reads."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reads: set[str] = set()

    def __getitem__(self, key: str) -> Any:
        self.reads.add(key)
        return super().__getitem__(key)


def field_dependencies(code: str) -> dict[str, frozenset[str]] | None:
    """Map each output field to the source fields its expression reads.

    Returns None when the function does not return a single dict literal; the
    caller must then treat every output field as depending on every read.
    """

    tree = ast.parse(code)
    function = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef)), None
    )
    if function is None or not function.args.args:
        return None
    arg = function.args.args[0].arg
    returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
    if len(returns) != 1 or not isinstance(returns[0].value, ast.Dict):
        return None
    deps: dict[str, frozenset[str]] = {}
    for key_node, value_node in zip(returns[0].value.keys, returns[0].value.values):
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            return None
        deps[key_node.value] = _subscript_reads(value_node, arg)
    return deps


def static_reads(code: str) -> frozenset[str]:
    tree = ast.parse(code)
    function = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef)), None
    )
    if function is None or not function.args.args:
        return frozenset()
    return _subscript_reads(function, function.args.args[0].arg)


def _subscript_reads(node: ast.AST, arg: str) -> frozenset[str]:
    return frozenset(
        sub.slice.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Subscript)
        and isinstance(sub.value, ast.Name)
        and sub.value.id == arg
        and isinstance(sub.slice, ast.Constant)
        and isinstance(sub.slice.value, str)
    )
