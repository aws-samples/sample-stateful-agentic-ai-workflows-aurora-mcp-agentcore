"""Build Gateway tool schemas from the signatures and docstrings of the catalog tools.

The Gateway accepts only a small schema vocabulary (type, properties, required, items,
description, minimum, maximum, maxLength, default, additionalProperties), so Pydantic's output
(titles, anyOf for Optional) is not published as is. tests/test_catalog_tool_schema.py compares the
result with what FastMCP serves over stdio, argument by argument.
"""

from __future__ import annotations

import inspect
import json
import re
import types
import typing
from typing import Any, Callable, Dict, List, Optional, Tuple

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}
_ENTRY = re.compile(r"^ {4}(\w+):\s*(.*)$")


def _json_type(function: str, argument: str, annotation: Any) -> Dict[str, Any]:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        members = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return _json_type(function, argument, members[0])
    elif origin is list:
        (item,) = typing.get_args(annotation)
        return {"type": "array", "items": _json_type(function, argument, item)}
    elif annotation in _JSON_TYPES:
        return {"type": _JSON_TYPES[annotation]}
    raise ValueError(
        f"{function}({argument}): cannot publish annotation {annotation!r}; "
        "use str, int, float, bool, List[...] or Optional of those"
    )


def _split_docstring(doc: Optional[str]) -> Tuple[str, Dict[str, str]]:
    lines = inspect.cleandoc(doc or "").splitlines()
    if "Args:" not in lines:
        return " ".join(" ".join(lines).split()), {}
    start = lines.index("Args:")
    description = " ".join(" ".join(lines[:start]).split())
    arguments: Dict[str, str] = {}
    current = None
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith(" "):
            break
        entry = _ENTRY.match(line)
        if entry:
            current = entry.group(1)
            arguments[current] = entry.group(2).strip()
        elif current and line.strip():
            arguments[current] += " " + line.strip()
    return description, arguments


def tool_schema(
    function: Callable,
    bounds: Optional[Dict[str, Tuple[int, int]]] = None,
    max_lengths: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """The Gateway tool definition for one function.

    Args:
        function: the tool function; its name, annotations, defaults and docstring are read.
        bounds: optional ``{argument: (minimum, maximum)}`` for integer arguments.
        max_lengths: optional ``{argument: longest text}`` for string arguments.

    Raises:
        ValueError: An annotation cannot be published.
    """
    signature = inspect.signature(function)
    hints = typing.get_type_hints(function)
    description, documented = _split_docstring(function.__doc__)
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for name, parameter in signature.parameters.items():
        prop = _json_type(function.__name__, name, hints[name])
        if name in documented:
            prop["description"] = documented[name]
        if name in (bounds or {}):
            prop["minimum"], prop["maximum"] = bounds[name]
        if name in (max_lengths or {}):
            prop["maxLength"] = max_lengths[name]
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        elif parameter.default is not None:
            prop["default"] = parameter.default
        properties[name] = prop
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return {"name": function.__name__, "description": description, "inputSchema": schema}


def catalog_schema() -> List[Dict[str, Any]]:
    """The MeridianCatalog target's tool definitions, in contract order."""
    from backend.tools import catalog_tools

    return [
        tool_schema(function, {
            argument: bound for (tool, argument), bound in catalog_tools.ARGUMENT_BOUNDS.items()
            if tool == name
        }, {
            argument: length
            for (tool, argument), length in catalog_tools.ARGUMENT_MAX_LENGTHS.items()
            if tool == name
        })
        for name, function in catalog_tools.CATALOG_FUNCTIONS.items()
    ]


def render(schema: List[Dict[str, Any]]) -> str:
    """The file text: two-space JSON and a final newline."""
    return json.dumps(schema, indent=2) + "\n"
