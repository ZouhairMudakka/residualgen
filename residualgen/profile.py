"""Conservative static check of an expanded OpenAI response schema.

Source: https://developers.openai.com/api/docs/guides/structured-outputs
Documentation inspected 2026-09-27. This checks a documented non-fine-tuned
profile, not live API acceptance, grammar compilation, or regex compatibility.
References must have been expanded. String minLength/maxLength are flagged as
unverified because the guide's explicit supported-string list omits them.
"""

from __future__ import annotations

from typing import Any


_ANNOTATIONS = {"title", "description", "default", "examples", "$comment"}
_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"}
_NUMERIC = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
_KEYS = {
    "object": {"type", "properties", "required", "additionalProperties"},
    "array": {"type", "items", "minItems", "maxItems"},
    "string": {"type", "enum", "pattern", "format"},
    "number": {"type", "enum"} | _NUMERIC,
    "integer": {"type", "enum"} | _NUMERIC,
    "boolean": {"type", "enum"},
    "null": {"type", "enum"},
}


def check_openai_profile(schema: Any) -> list[str]:
    """Return diagnostic strings; [] means only this static check passed.

    Counts nesting conservatively as JSON object/array container levels,
    including the root. anyOf does not itself add a JSON container. Annotation
    values are ignored. This helper does not replace JSON Schema validation.
    """
    issues: list[str] = []
    properties_count = strings_count = enum_count = nodes = 0

    def issue(path: str, message: str) -> None:
        issues.append(f"{path}: {message}")

    if type(schema) is not dict or schema.get("type") != "object" or "anyOf" in schema:
        issue("$", "Root must be an object without anyOf")

    def visit(node: Any, path: str, container_depth: int, ancestors: frozenset[int]) -> None:
        nonlocal properties_count, strings_count, enum_count, nodes
        nodes += 1
        if nodes > 100_000:
            if nodes == 100_001:
                issue("$", "Static checker node limit exceeded")
            return
        if type(node) is not dict:
            issue(path, "Schema must be an object")
            return
        if id(node) in ancestors:
            issue(path, "Cyclic Python schema objects are unsupported")
            return
        if len(ancestors) > 128:
            issue(path, "Static checker traversal limit exceeded")
            return
        ancestors = ancestors | {id(node)}
        if "$ref" in node or "$defs" in node:
            issue(path, "This expanded-schema profile requires references and definitions to be expanded")
        if "anyOf" in node:
            unknown = set(node) - _ANNOTATIONS - {"anyOf"}
            if unknown:
                issue(path, f"Unverified anyOf siblings: {sorted(unknown, key=str)}")
            branches = node["anyOf"]
            if type(branches) is not list or not branches:
                issue(path, "anyOf must be a nonempty list")
                return
            for index, branch in enumerate(branches):
                visit(branch, f"{path}.anyOf[{index}]", container_depth, ancestors)
            return
        kind = node.get("type")
        if type(kind) is not str or kind not in _KEYS:
            issue(path, "A single supported literal type is required in this conservative profile")
            return
        unknown = set(node) - _ANNOTATIONS - _KEYS[kind]
        if unknown:
            issue(path, f"Unsupported or unverified keywords: {sorted(unknown, key=str)}")
        depth = container_depth + int(kind in {"object", "array"})
        if depth > 10:
            issue(path, "Nesting exceeds 10 object/array container levels")
            return
        if kind == "object":
            props = node.get("properties")
            if type(props) is not dict or any(type(key) is not str for key in props):
                issue(path, "properties must be an object with string names")
                return
            properties_count += len(props)
            strings_count += sum(len(key) for key in props)
            if node.get("additionalProperties") is not False:
                issue(path, "additionalProperties must be false")
            required = node.get("required")
            if (type(required) is not list or any(type(key) is not str for key in required)
                    or len(required) != len(set(required)) or set(required) != set(props)):
                issue(path, "required must name every property exactly once")
            for key, child in props.items():
                visit(child, f"{path}.properties[{key!r}]", depth, ancestors)
        elif kind == "array":
            if "items" not in node:
                issue(path, "Homogeneous items schema is required")
            else:
                visit(node["items"], f"{path}.items", depth, ancestors)
            for bound in ("minItems", "maxItems"):
                if bound in node and (type(node[bound]) is not int or node[bound] < 0):
                    issue(path, f"{bound} must be a nonnegative integer")
            if (type(node.get("minItems")) is int and type(node.get("maxItems")) is int
                    and node["minItems"] > node["maxItems"]):
                issue(path, "minItems exceeds maxItems")
        if "enum" in node:
            values = node["enum"]
            if type(values) is not list or not values:
                issue(path, "enum must be a nonempty list")
            else:
                enum_count += len(values)
                string_size = sum(len(value) for value in values if type(value) is str)
                strings_count += string_size
                if len(values) > 250 and string_size > 15_000:
                    issue(path, "An enum with more than 250 values exceeds 15,000 string characters")
        if kind == "string" and "pattern" in node and type(node["pattern"]) is not str:
            issue(path, "pattern must be a string; regex dialect compatibility remains untested")
        if kind == "string" and "format" in node and (type(node["format"]) is not str or node["format"] not in _FORMATS):
            issue(path, "format is not in the documented supported list")

    visit(schema, "$", 0, frozenset())
    if properties_count > 5_000:
        issue("$", f"Total property count {properties_count} exceeds 5,000")
    if strings_count > 120_000:
        issue("$", f"Counted property-name/enum string characters {strings_count} exceed 120,000")
    if enum_count > 1_000:
        issue("$", f"Total enum value count {enum_count} exceeds 1,000")
    return issues
