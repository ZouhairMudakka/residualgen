"""Compile a retained JSON frontier into a fresh continuation contract.

This is deliberately a small schema language, not arbitrary JSON Schema.
Compilation specializes whole union branches; it never unions their fields
independently. A continuation accepted by the emitted schema must assemble to
an original-schema value while preserving the sealed values. Unsupported
assertions fail closed. Native-provider acceptance is not claimed here.
"""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .parser import Node


class UnsupportedSchema(ValueError):
    """The source is outside the explicitly implemented schema fragment."""


class InfeasiblePrefix(ValueError):
    """No source-schema value can preserve the supplied retained frontier."""


class InvalidContinuation(ValueError):
    """A supplied payload/value violates the continuation or retention contract."""


MAX_DEPTH = 48
MAX_NODES = 20_000
MAX_BYTES = 1_000_000
_ANNOTATIONS = {"title", "description", "default", "examples", "$comment"}
_PRIMITIVES = {"string", "number", "integer", "boolean", "null"}
_SCALAR_KEYS = {
    "string": {"minLength", "maxLength", "pattern"},
    "number": {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"},
    "integer": {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"},
    "boolean": set(),
    "null": set(),
}


def _bounded_json(value: Any, error: type[ValueError]) -> None:
    """Reject non-JSON Python values, cycles, and excessive local work."""
    stack = [(value, 0, frozenset())]
    count = 0
    while stack:
        item, depth, ancestors = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise error("JSON exceeds node/depth bounds")
        if type(item) in (dict, list):
            if id(item) in ancestors:
                raise error("Cyclic Python objects are not JSON")
            next_ancestors = ancestors | {id(item)}
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise error("JSON object keys must be strings")
                if any(len(key) > MAX_BYTES for key in item):
                    raise error("JSON key exceeds size bound")
                children = item.values()
            else:
                children = item
            stack.extend((child, depth + 1, next_ancestors) for child in children)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise error(f"Not a JSON value: {type(item).__name__}")
        elif type(item) is float and not math.isfinite(item):
            raise error("Non-finite JSON number")
        elif type(item) is str and len(item) > MAX_BYTES:
            raise error("JSON string exceeds size bound")
    try:
        size = len(json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    except (ValueError, OverflowError) as exc:
        raise error("JSON serialization exceeds supported bounds") from exc
    if size > MAX_BYTES:
        raise error("JSON exceeds serialized size bound")


def _empty() -> dict:
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _normalize(source: dict) -> dict:
    _bounded_json(source, UnsupportedSchema)
    if type(source) is not dict:
        raise UnsupportedSchema("Source schema must be an object")
    try:
        Draft202012Validator.check_schema(source)
    except SchemaError as exc:
        raise UnsupportedSchema(f"Invalid JSON Schema: {exc.message}") from exc
    visited = 0

    def visit(schema: Any, active_refs: frozenset[str], depth: int) -> dict:
        nonlocal visited
        visited += 1
        if visited > MAX_NODES or depth > MAX_DEPTH:
            raise UnsupportedSchema("Expanded schema exceeds node/depth bounds")
        if type(schema) is not dict:
            raise UnsupportedSchema("Boolean schemas are outside this fragment")
        annotations = {k: copy.deepcopy(v) for k, v in schema.items() if k in _ANNOTATIONS}
        if "$defs" in schema:
            # Unused definitions are checked too: unsupported assertions are
            # never silently accepted merely because no current branch uses one.
            if type(schema["$defs"]) is not dict:
                raise UnsupportedSchema("$defs must be an object")
            for definition in schema["$defs"].values():
                visit(definition, active_refs, depth + 1)
        structural = set(schema) - _ANNOTATIONS - {"$defs", "$schema"}
        if "$schema" in schema and schema["$schema"] not in {
            "https://json-schema.org/draft/2020-12/schema",
            "https://json-schema.org/draft/2020-12/schema#",
        }:
            raise UnsupportedSchema("Only the 2020-12 dialect is supported")
        if "$ref" in schema:
            if structural != {"$ref"}:
                raise UnsupportedSchema("$ref assertion siblings are unsupported")
            raw_ref = schema["$ref"]
            if not isinstance(raw_ref, str) or not raw_ref.startswith("#/"):
                raise UnsupportedSchema("Only local $defs references are supported")
            if re.search(r"%(?![0-9a-fA-F]{2})", raw_ref):
                raise UnsupportedSchema("Malformed URI-fragment escape in reference")
            try:
                # RFC 6901 URI-fragment representation: percent-decode once,
                # split pointer tokens, then decode ~1/~0 within each token.
                ref = unquote(raw_ref, encoding="utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise UnsupportedSchema("Invalid UTF-8 URI fragment") from exc
            if not ref.startswith("#/$defs/"):
                raise UnsupportedSchema("Only local $defs references are supported")
            if ref in active_refs:
                raise UnsupportedSchema("Recursive references are unsupported")
            target: Any = source
            for token in ref[2:].split("/"):
                if re.search(r"~(?![01])", token):
                    raise UnsupportedSchema("Malformed JSON Pointer escape")
                token = token.replace("~1", "/").replace("~0", "~")
                if type(target) is not dict or token not in target:
                    raise UnsupportedSchema(f"Unresolved local reference: {ref}")
                target = target[token]
            result = visit(target, active_refs | {ref}, depth + 1)
            return {**result, **annotations}
        if "anyOf" in schema:
            if structural != {"anyOf"}:
                raise UnsupportedSchema("anyOf assertion siblings are unsupported")
            return {**annotations, "anyOf": [visit(s, active_refs, depth + 1) for s in schema["anyOf"]]}
        kind = schema.get("type")
        if type(kind) is not str:
            raise UnsupportedSchema("A literal type or a standalone anyOf is required")
        if kind == "object":
            allowed = {"type", "properties", "required", "additionalProperties"}
            if structural - allowed:
                raise UnsupportedSchema(f"Unsupported object keywords: {sorted(structural - allowed)}")
            props = schema.get("properties", {})
            if schema.get("additionalProperties") is not False:
                raise UnsupportedSchema("Objects must explicitly forbid additionalProperties")
            if set(schema.get("required", [])) != set(props):
                raise UnsupportedSchema("Every declared object property must be required")
            return {**annotations, **_object({k: visit(s, active_refs, depth + 1) for k, s in props.items()})}
        if kind == "array":
            allowed = {"type", "items", "minItems", "maxItems"}
            if structural - allowed or "items" not in schema:
                raise UnsupportedSchema("Arrays require homogeneous items; only minItems/maxItems are supported")
            result = {**annotations, "type": "array", "items": visit(schema["items"], active_refs, depth + 1)}
            result.update({key: schema[key] for key in ("minItems", "maxItems") if key in schema})
            return result
        if kind not in _PRIMITIVES:
            raise UnsupportedSchema(f"Unsupported type: {kind}")
        allowed = {"type", "enum", "const"} | _SCALAR_KEYS[kind]
        if structural - allowed:
            raise UnsupportedSchema(f"Unsupported scalar keywords: {sorted(structural - allowed)}")
        if "const" in schema and "enum" in schema:
            raise UnsupportedSchema("Combined const and enum are unsupported")
        values = schema.get("enum", [schema["const"]] if "const" in schema else [])
        if any(type(value) in (dict, list) for value in values):
            raise UnsupportedSchema("Only scalar enum/const values are supported")
        result = {k: copy.deepcopy(v) for k, v in schema.items() if k not in {"$defs", "$schema", "const"}}
        if "const" in schema:
            result["enum"] = [copy.deepcopy(schema["const"])]
        return result

    result = visit(source, frozenset(), 0)
    _bounded_json(result, UnsupportedSchema)
    return result


def _copy_prefix(node: Node) -> Node:
    count = 0
    serialized_size = 0

    def walk(current: Node, depth: int) -> Node:
        nonlocal count, serialized_size
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH or not isinstance(current, Node):
            raise InfeasiblePrefix("Invalid or excessive prefix frontier")
        if current.kind == "complete":
            _bounded_json(current.value, InfeasiblePrefix)
            serialized_size += len(json.dumps(current.value, ensure_ascii=True, allow_nan=False))
            if serialized_size > MAX_BYTES:
                raise InfeasiblePrefix("Retained prefix exceeds size bound")
            return Node("complete", copy.deepcopy(current.value))
        if current.kind == "missing":
            if current.value is not None:
                raise InfeasiblePrefix("Missing node must contain None")
            return Node("missing", None)
        if current.kind == "object" and type(current.value) is dict:
            if any(type(key) is not str for key in current.value):
                raise InfeasiblePrefix("Prefix object keys must be strings")
            serialized_size += sum(len(json.dumps(key, ensure_ascii=True)) for key in current.value)
            if serialized_size > MAX_BYTES:
                raise InfeasiblePrefix("Retained prefix exceeds size bound")
            return Node("object", {k: walk(v, depth + 1) for k, v in current.value.items()}, needs_key=current.needs_key)
        if current.kind == "array" and type(current.value) is tuple:
            if any(not isinstance(child, Node) or child.kind != "complete" for child in current.value[:-1]):
                raise InfeasiblePrefix("Only the last array item may be incomplete")
            return Node("array", tuple(walk(v, depth + 1) for v in current.value))
        raise InfeasiblePrefix(f"Invalid frontier node: {current.kind}")

    return walk(node, 0)


def _residual(schema: dict, node: Node) -> dict:
    if node.kind == "complete":
        if not Draft202012Validator(schema).is_valid(node.value):
            raise InfeasiblePrefix("A sealed value violates the source schema")
        return _empty()
    if node.kind == "missing":
        return copy.deepcopy(schema)
    if "anyOf" in schema:
        branches = []
        for branch in schema["anyOf"]:
            try:
                residual = _residual(branch, node)
            except InfeasiblePrefix:
                continue
            if not any(_equal(residual, other) for other in branches):
                branches.append(residual)
        if not branches:
            raise InfeasiblePrefix("No union branch can preserve the retained values")
        return branches[0] if len(branches) == 1 else {"anyOf": branches}
    if node.kind == "object":
        if schema.get("type") != "object":
            raise InfeasiblePrefix("Open object conflicts with source type")
        props = schema["properties"]
        if set(node.value) - set(props):
            raise InfeasiblePrefix("Prefix contains undeclared object properties")
        if node.needs_key and not (set(props) - set(node.value)):
            raise InfeasiblePrefix("An unfinished new key has no remaining declared property")
        remainder = {}
        for key, child_schema in props.items():
            child = node.value.get(key, Node("missing", None))
            transformed = _residual(child_schema, child)
            if child.kind != "complete":
                remainder[key] = transformed
        return _object(remainder)
    if node.kind == "array":
        if schema.get("type") != "array":
            raise InfeasiblePrefix("Open array conflicts with source type")
        items = node.value
        has_frontier = bool(items and items[-1].kind != "complete")
        completed = items[:-1] if has_frontier else items
        for child in completed:
            _residual(schema["items"], child)
        consumed = len(completed) + int(has_frontier)
        if consumed > schema.get("maxItems", math.inf):
            raise InfeasiblePrefix("Retained array already exceeds maxItems")
        tail = {"type": "array", "items": copy.deepcopy(schema["items"]), "minItems": max(0, schema.get("minItems", 0) - consumed)}
        if "maxItems" in schema:
            tail["maxItems"] = schema["maxItems"] - consumed
            if tail["maxItems"] == 0:
                # No item can occur in this tail, so its item schema is
                # semantically irrelevant. Avoid duplicating a potentially
                # large source item schema behind an always-empty array.
                tail["items"] = {"type": "null"}
        properties = {}
        if has_frontier:
            properties["item"] = _residual(schema["items"], items[-1])
        properties["tail"] = tail
        return _object(properties)
    raise InfeasiblePrefix("Frontier shape is incompatible with schema")


def _equal(left: Any, right: Any) -> bool:
    # JSON Schema numeric equality treats 1 and 1.0 alike, but never true and 1.
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _assemble(node: Node, continuation: Any) -> Any:
    if node.kind == "complete":
        return copy.deepcopy(node.value)
    if node.kind == "missing":
        return copy.deepcopy(continuation)
    if node.kind == "object":
        result = {k: copy.deepcopy(v.value) for k, v in node.value.items() if v.kind == "complete"}
        for key, value in continuation.items():
            result[key] = _assemble(node.value.get(key, Node("missing", None)), value)
        return result
    completed = [copy.deepcopy(child.value) for child in node.value if child.kind == "complete"]
    if node.value and node.value[-1].kind != "complete":
        completed.append(_assemble(node.value[-1], continuation["item"]))
    return completed + copy.deepcopy(continuation["tail"])


def _project(node: Node, full: Any) -> Any:
    if node.kind == "complete":
        if not _equal(node.value, full):
            raise InvalidContinuation("Full value changes a sealed prefix value")
        return {}
    if node.kind == "missing":
        return copy.deepcopy(full)
    if node.kind == "object":
        if type(full) is not dict or set(node.value) - set(full):
            raise InvalidContinuation("Full value does not contain the object frontier")
        for key, child in node.value.items():
            if child.kind == "complete":
                _project(child, full[key])
        return {key: _project(node.value.get(key, Node("missing", None)), value) for key, value in full.items() if key not in node.value or node.value[key].kind != "complete"}
    if type(full) is not list or len(full) < len(node.value):
        raise InvalidContinuation("Full value does not contain the array frontier")
    result = {}
    for index, child in enumerate(node.value):
        piece = _project(child, full[index])
        if child.kind != "complete":
            result["item"] = piece
    result["tail"] = copy.deepcopy(full[len(node.value):])
    return result


def _require_valid(validator: Draft202012Validator, value: Any, label: str) -> None:
    error = next(validator.iter_errors(value), None)
    if error is not None:
        raise InvalidContinuation(f"{label}: {error.message}")


@dataclass(frozen=True)
class Plan:
    _schema: dict
    _prefix: Node
    _source_validator: Draft202012Validator
    _payload_validator: Draft202012Validator

    @property
    def schema(self) -> dict:
        """A fresh copy; caller mutation cannot weaken Plan validation."""
        return copy.deepcopy(self._schema)

    def merge(self, payload: Any) -> Any:
        _bounded_json(payload, InvalidContinuation)
        _require_valid(self._payload_validator, payload, "Invalid continuation")
        full = _assemble(self._prefix, payload["resume"])
        _bounded_json(full, InvalidContinuation)
        _require_valid(self._source_validator, full, "Assembled value violates original schema")
        return full

    def project(self, full_value: Any) -> dict:
        """Project a valid retained-value completion for an independent oracle."""
        _bounded_json(full_value, InvalidContinuation)
        _require_valid(self._source_validator, full_value, "Invalid full value")
        payload = {"resume": _project(self._prefix, full_value)}
        _require_valid(self._payload_validator, payload, "Value is not a permitted completion")
        return payload


def compile_plan(schema: dict, prefix: Node) -> Plan:
    """Create a bounded, immutable-snapshot continuation plan or refuse it."""
    normalized = _normalize(schema)
    snapshot = _copy_prefix(prefix)
    residual = _residual(normalized, snapshot)
    wire_schema = _object({"resume": residual})
    _bounded_json(wire_schema, UnsupportedSchema)
    Draft202012Validator.check_schema(wire_schema)
    source_snapshot = copy.deepcopy(schema)
    return Plan(wire_schema, snapshot, Draft202012Validator(source_snapshot), Draft202012Validator(wire_schema))
