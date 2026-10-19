# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Read authored mappings without silently accepting duplicate keys."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import yaml

MAX_DOCUMENT_DEPTH = 64
MAX_DOCUMENT_NODES = 100_000
_CONTAINERS = (dict, list, tuple, set, frozenset)


def _children(value: Any) -> tuple[Any, ...]:
    return tuple(value.values()) if isinstance(value, dict) else tuple(value)


def _subtree_size(value: Any, sizes: dict[int, tuple[int, int]], max_nodes: int) -> tuple[int, int]:
    height, count = 1, 1
    for child in _children(value):
        child_height, child_count = sizes[id(child)] if isinstance(child, _CONTAINERS) else (0, 1)
        height = max(height, child_height + 1)
        count += child_count
        if count > max_nodes:
            raise ValueError(f"Document exceeds {max_nodes} expanded values")
    return height, count


def validate_document_graph(
    document: Any,
    *,
    max_depth: int = MAX_DOCUMENT_DEPTH,
    max_nodes: int = MAX_DOCUMENT_NODES,
) -> None:
    """Bound nesting and alias expansion without repeatedly traversing shared nodes."""
    if type(max_depth) is not int or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")
    if type(max_nodes) is not int or max_nodes < 1:
        raise ValueError("max_nodes must be a positive integer")
    sizes: dict[int, tuple[int, int]] = {}
    active: set[int] = set()
    pending = [(document, 1, False)]
    while pending:
        value, depth, leaving = pending.pop()
        if not isinstance(value, _CONTAINERS):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Nonfinite numeric value is invalid in a document")
            continue
        if depth > max_depth:
            raise ValueError(f"Document nesting exceeds {max_depth} levels")
        identity = id(value)
        if leaving:
            sizes[identity] = _subtree_size(value, sizes, max_nodes)
            active.remove(identity)
        elif identity in active:
            raise ValueError("Recursive document aliases are not supported")
        elif identity not in sizes:
            children = _children(value)
            if len(children) >= max_nodes:
                raise ValueError(f"Document exceeds {max_nodes} expanded values")
            active.add(identity)
            pending.append((value, depth, True))
            pending.extend((child, depth + 1, False) for child in reversed(children))
            continue
        if depth + sizes[identity][0] - 1 > max_depth:
            raise ValueError(f"Document nesting exceeds {max_depth} levels")


def safe_yaml_error(error: yaml.YAMLError) -> str:
    """Keep an actionable location without echoing tags, values or source text."""
    mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
    location = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "document"
    return f"{location}: Invalid YAML syntax, tag or document structure"


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate key: {key}")
        result[key] = value
    return result


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader: UniqueLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ValueError("Mapping keys must be strings")
        if key in result:
            raise ValueError(f"Duplicate key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse_document(
    content: str,
    suffix: str,
    *,
    max_depth: int = MAX_DOCUMENT_DEPTH,
    max_nodes: int = MAX_DOCUMENT_NODES,
) -> Any:
    try:
        if suffix.lower() == ".json":
            document = json.loads(
                content, object_pairs_hook=unique_pairs, parse_constant=_invalid_constant
            )
        elif suffix.lower() in {".yaml", ".yml"}:
            document = yaml.load(content, Loader=UniqueLoader)
        else:
            raise ValueError(f"Expected JSON or YAML, got {suffix}")
    except RecursionError as error:
        raise ValueError(f"Document nesting exceeds {max_depth} levels") from error
    validate_document_graph(document, max_depth=max_depth, max_nodes=max_nodes)
    return document


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Nonfinite JSON number is invalid: {value}")


def read_document(path: Path) -> dict[str, Any]:
    data = parse_document(path.read_text(encoding="utf-8"), path.suffix)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping: {path}")
    return data
