"""A strict parser for a valid, possibly interrupted JSON prefix.

No repair is attempted. Incomplete primitive values are rolled back, never
coerced into apparently complete data. Nodes retain structure, not invented
closing brackets. The compiler operates on this sealed structure, rather than
claiming to continue the literal bytes or the model's hidden state.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any


class PrefixError(ValueError):
    """The input is not a supported, valid JSON prefix."""


@dataclass(frozen=True)
class Node:
    kind: str
    value: Any = None
    needs_key: bool = False


_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_NUMBER_PREFIX = re.compile(
    r"(?:-|-?(?:0|[1-9][0-9]*)(?:\.[0-9]*)?|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?[eE][+-]?[0-9]*)"
)
_WS = " \t\r\n"
_DELIMITERS = _WS + ",]}"


class _Reader:
    def __init__(self, text: str, max_depth: int, max_nodes: int):
        self.text = text
        self.pos = 0
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.nodes = 0

    def error(self, message: str) -> PrefixError:
        return PrefixError(f"{message} at character {self.pos}")

    def ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in _WS:
            self.pos += 1

    def string(self) -> tuple[bool, str | None]:
        start = self.pos
        self.pos += 1
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == '"':
                self.pos += 1
                try:
                    value = json.loads(self.text[start : self.pos])
                except (ValueError, UnicodeError) as exc:
                    raise self.error("Invalid string") from exc
                # Lone surrogate values are not portable JSON text for APIs.
                if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                    raise self.error("Lone Unicode surrogate is unsupported")
                return True, value
            if ord(ch) < 0x20:
                raise self.error("Unescaped control character")
            if ch == "\\":
                self.pos += 1
                if self.pos == len(self.text):
                    return False, None
                escape = self.text[self.pos]
                if escape == "u":
                    self.pos += 1
                    for _ in range(4):
                        if self.pos == len(self.text):
                            return False, None
                        if self.text[self.pos] not in "0123456789abcdefABCDEF":
                            raise self.error("Invalid Unicode escape")
                        self.pos += 1
                    continue
                if escape not in '"\\/bfnrt':
                    raise self.error("Invalid escape")
            self.pos += 1
        return False, None

    def value(self, depth: int = 0) -> Node:
        if depth > self.max_depth:
            raise self.error("Nesting limit exceeded")
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise self.error("Node limit exceeded")
        self.ws()
        if self.pos == len(self.text):
            return Node("missing")
        ch = self.text[self.pos]
        if ch == "{":
            return self.object(depth)
        if ch == "[":
            return self.array(depth)
        if ch == '"':
            complete, value = self.string()
            return Node("complete", value) if complete else Node("missing")
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in _DELIMITERS:
            self.pos += 1
        word = self.text[start : self.pos]
        if not word:
            raise self.error("Expected value")
        at_end = self.pos == len(self.text)
        if word[0] in "tfn":
            literals = {"true": True, "false": False, "null": None}
            if word in literals:
                # A delimiter commits numbers and literals; EOF alone does not.
                return Node("missing") if at_end else Node("complete", literals[word])
            if at_end and any(lit.startswith(word) for lit in literals):
                return Node("missing")
            raise self.error("Invalid literal")
        if word[0] in "-0123456789":
            if at_end:
                if not _NUMBER_PREFIX.fullmatch(word):
                    raise self.error("Invalid number prefix")
                return Node("missing")
            if not _NUMBER.fullmatch(word):
                raise self.error("Invalid number")
            try:
                value = json.loads(word)
            except ValueError as exc:
                raise self.error("Invalid number") from exc
            if isinstance(value, float) and not math.isfinite(value):
                raise self.error("Non-finite number is unsupported")
            return Node("complete", value)
        raise self.error("Unexpected token")

    def object(self, depth: int) -> Node:
        self.pos += 1
        fields: dict[str, Node] = {}
        after_comma = False
        while True:
            self.ws()
            if self.pos == len(self.text):
                # A comma requires a future key. The compiler must reject the
                # prefix if the closed schema has no unobserved key left.
                return Node("object", fields, needs_key=after_comma)
            if self.text[self.pos] == "}":
                if after_comma:
                    raise self.error("Trailing comma")
                self.pos += 1
                return Node("complete", {k: v.value for k, v in fields.items()})
            if self.text[self.pos] != '"':
                raise self.error("Expected object key")
            complete, key = self.string()
            if not complete:
                return Node("object", fields, needs_key=True)
            assert key is not None
            if key in fields:
                raise self.error("Duplicate object key")
            self.ws()
            if self.pos == len(self.text):
                fields[key] = Node("missing")
                return Node("object", fields)
            if self.text[self.pos] != ":":
                raise self.error("Expected colon")
            self.pos += 1
            child = self.value(depth + 1)
            fields[key] = child
            if child.kind != "complete":
                return Node("object", fields)
            self.ws()
            if self.pos == len(self.text):
                return Node("object", fields)
            ch = self.text[self.pos]
            if ch == "}":
                self.pos += 1
                return Node("complete", {k: v.value for k, v in fields.items()})
            if ch != ",":
                raise self.error("Expected comma or closing brace")
            self.pos += 1
            after_comma = True

    def array(self, depth: int) -> Node:
        self.pos += 1
        items: list[Node] = []
        self.ws()
        if self.pos == len(self.text):
            return Node("array", tuple(items))
        if self.text[self.pos] == "]":
            self.pos += 1
            return Node("complete", [])
        while True:
            child = self.value(depth + 1)
            items.append(child)
            if child.kind != "complete":
                return Node("array", tuple(items))
            self.ws()
            if self.pos == len(self.text):
                return Node("array", tuple(items))
            ch = self.text[self.pos]
            if ch == "]":
                self.pos += 1
                return Node("complete", [item.value for item in items])
            if ch != ",":
                raise self.error("Expected comma or closing bracket")
            self.pos += 1


def parse_prefix(
    text: str, *, max_chars: int = 1_000_000, max_depth: int = 64, max_nodes: int = 100_000
) -> Node:
    """Parse a JSON prefix with bounded work and no inferred primitive values.

    Objects have required keys supplied by the schema. An incomplete key is
    discarded. Incomplete strings/numbers/literals are regenerated as a whole.
    Input must be a contiguous prefix from one response, not missing/reordered
    chunks. A refusal or arbitrary malformed output is not recoverable here.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if len(text) > max_chars:
        raise PrefixError("Input length limit exceeded")
    if min(max_chars, max_depth, max_nodes) < 1:
        raise ValueError("Parser limits must be positive")
    reader = _Reader(text, max_depth, max_nodes)
    node = reader.value()
    reader.ws()
    if reader.pos != len(text):
        raise reader.error("Unexpected data after root value")
    return node
