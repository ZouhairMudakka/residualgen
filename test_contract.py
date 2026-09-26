"""Independent finite-language and mutation tests; no model calls.

The oracle uses jsonschema plus the retained-value relation, not compiler internals.
Projected payloads are explicitly oracle fixtures, never alleged model responses.
"""
from __future__ import annotations

import copy
import itertools
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from residualgen import compile_plan, parse_prefix
from residualgen.parser import PrefixError
from residualgen.compiler import InfeasiblePrefix, InvalidContinuation, UnsupportedSchema


METRICS = {}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def json_equal(left, right):
    """JSON equality without Python's accidental True == 1 equality."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def preserves(node, value):
    """Independent retained-value relation for the public ParserNode protocol."""
    if node.kind == "missing":
        return True
    if node.kind == "complete":
        return json_equal(node.value, value)
    if node.kind == "object":
        return (
            isinstance(value, dict)
            and all(k in value and preserves(child, value[k]) for k, child in node.value.items())
            and (not node.needs_key or bool(set(value) - set(node.value)))
        )
    if node.kind == "array":
        return (
            isinstance(value, list)
            and len(value) >= len(node.value)
            and all(preserves(child, value[i]) for i, child in enumerate(node.value))
        )
    raise AssertionError(f"Unexpected public node kind {node.kind}")


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def language_cases():
    """Explicit finite universes, authored independently from the schema compiler."""
    scalar = obj({"name": {"type": "string", "enum": ["A", "B"]}, "quota": {"type": "integer", "minimum": 0, "maximum": 1}})
    scalar_values = [dict(name=a, quota=b) for a, b in itertools.product(["A", "B", "bad"], [-1, 0, 1, 2, True])]
    yield "scalar", scalar, scalar_values, [{"name": "A", "quota": 1}, {"name": "B", "quota": 0}]

    arrays = obj({"items": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}, "minItems": 2, "maxItems": 3}})
    array_values = [{"items": list(values)} for size in range(5) for values in itertools.product(["a", "b"], repeat=size)]
    yield "bounds", arrays, array_values, [{"items": ["a", "b", "a"]}, {"items": ["b", "a"]}]

    item = obj({"label": {"type": "string", "enum": ["A", "B"]}, "active": {"type": "boolean"}})
    nested = obj({"version": {"type": "integer", "enum": [1]}, "rows": {"type": "array", "items": item, "minItems": 1, "maxItems": 2}})
    item_values = [dict(label=a, active=b) for a, b in itertools.product(["A", "B"], [False, True])]
    nested_values = [{"version": 1, "rows": list(rows)} for size in range(4) for rows in itertools.product(item_values, repeat=size)]
    yield "nested", nested, nested_values, [{"version": 1, "rows": [{"label": "A", "active": True}, {"label": "B", "active": False}]}]

    branch = {"anyOf": [obj({"tag": {"type": "string", "enum": ["A"]}, "value": {"type": "string", "enum": ["x"]}}), obj({"tag": {"type": "string", "enum": ["B"]}, "value": {"type": "string", "enum": ["y"]}})]}
    correlated = obj({"before": {"type": "string", "enum": ["kept"]}, "choice": branch, "after": {"type": "boolean"}})
    correlated_values = [dict(before="kept", choice=dict(tag=a, value=b), after=c) for a, b, c in itertools.product(["A", "B"], ["x", "y"], [False, True])]
    yield "correlation", correlated, correlated_values, [correlated_values[0], correlated_values[-1]]

    empty = obj({"items": {"type": "array", "items": {"type": "integer", "enum": [0]}, "minItems": 0, "maxItems": 0}, "end": {"type": "boolean"}})
    yield "zero", empty, [{"items": a, "end": b} for a, b in itertools.product([[], [0], [0, 0]], [False, True])], [{"items": [], "end": True}]


def walk(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + (index,))


def replaced(value, path, replacement):
    if not path:
        return copy.deepcopy(replacement)
    result = copy.deepcopy(value)
    parent = result
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = copy.deepcopy(replacement)
    return result


def payload_mutations(payloads):
    """Mutation closure independent of the residual schema's asserted language."""
    seen = set()
    atoms = [None, False, True, -1, 0, 1, 2, "", "A", "B", "x", "y", "bad", [], {}]
    for payload in payloads:
        for path, value in walk(payload):
            for replacement in atoms:
                candidate = replaced(payload, path, replacement)
                key = encode(candidate)
                if key not in seen:
                    seen.add(key)
                    yield candidate
            if isinstance(value, dict):
                for key in value:
                    smaller = dict(value)
                    del smaller[key]
                    yield replaced(payload, path, smaller)
                yield replaced(payload, path, {**value, "unexpected": 0})
            elif isinstance(value, list):
                for smaller in (value[:i] + value[i + 1:] for i in range(len(value))):
                    yield replaced(payload, path, smaller)
                yield replaced(payload, path, value + value)
                yield replaced(payload, path, list(reversed(value)))
        # Recombine two independent alternatives to expose lost branch correlation.
        for alternative in payloads:
            for path, replacement in walk(alternative):
                try:
                    yield replaced(payload, path, replacement)
                except (KeyError, IndexError, TypeError):
                    pass


class ContractTests(unittest.TestCase):
    def test_every_character_cut_preserves_finite_completion_language(self):
        checked = candidates_checked = prefixes_checked = rejected = 0
        for label, schema, candidates, seeds in language_cases():
            original = Draft202012Validator(schema)
            valid = [value for value in candidates if original.is_valid(value)]
            for seed in seeds:
                text = encode(seed)
                for cut in range(len(text) + 1):
                    with self.subTest(case=label, cut=cut):
                        prefixes_checked += 1
                        node = parse_prefix(text[:cut])
                        plan = compile_plan(schema, node)
                        Draft202012Validator.check_schema(plan.schema)
                        residual = Draft202012Validator(plan.schema)
                        for candidate in candidates:
                            candidates_checked += 1
                            expected = original.is_valid(candidate) and preserves(node, candidate)
                            if not expected:
                                with self.assertRaises((InvalidContinuation, InfeasiblePrefix, ValueError)):
                                    plan.project(candidate)
                                rejected += 1
                                continue
                            payload = plan.project(candidate)
                            self.assertTrue(residual.is_valid(payload), (label, cut, payload, plan.schema))
                            self.assertTrue(json_equal(plan.merge(payload), candidate))
                            checked += 1
                        self.assertTrue(any(preserves(node, value) for value in valid))
        self.assertGreater(checked, 500)
        METRICS["finite_language"] = {"schemas": 5, "prefix_checks": prefixes_checked, "candidate_checks": candidates_checked, "compatible_valid_candidates": checked, "invalid_or_prefix_incompatible_candidates": rejected}

    def test_accepted_mutated_payloads_always_reconstruct_valid_frozen_values(self):
        accepted = rejected = 0
        for label, schema, candidates, seeds in language_cases():
            original = Draft202012Validator(schema)
            valid = [value for value in candidates if original.is_valid(value)]
            text = encode(seeds[0])
            # Structural boundaries plus evenly spaced cuts, not compiler-chosen paths.
            cuts = {0, len(text), len(text) // 2, 1}
            cuts.update(i + 1 for i, char in enumerate(text) if char in "[:,")
            for cut in sorted(cuts):
                node = parse_prefix(text[:cut])
                plan = compile_plan(schema, node)
                residual = Draft202012Validator(plan.schema)
                payloads = [plan.project(v) for v in valid if preserves(node, v)]
                for payload in payload_mutations(payloads):
                    if residual.is_valid(payload):
                        accepted += 1
                        merged = plan.merge(payload)  # A schema-accepted payload must not fail here.
                        self.assertTrue(original.is_valid(merged), (label, cut, payload, merged))
                        self.assertTrue(preserves(node, merged), (label, cut, payload, merged))
                    else:
                        rejected += 1
                        with self.assertRaises((InvalidContinuation, ValueError)):
                            plan.merge(payload)
        self.assertGreater(accepted, 100)
        self.assertGreater(rejected, 1000)
        METRICS["payload_mutations"] = {"accepted_payload_checks": accepted, "rejected_payload_checks": rejected, "note": "Mutation checks include repeated payload values at distinct prefixes; these are operation counts, not unique samples or estimated success rates."}

    def test_lexical_retention_is_conservative(self):
        # EOF numbers/literals remain unfinished; delimiters seal them.
        for raw in ["1", "-1", "1e", "1e+", "1.2", "true", "false", "null"]:
            self.assertEqual(parse_prefix(raw).kind, "missing", raw)
        for raw, expected in [("1 ", 1), ("true ", True), ('"ready"', "ready")]:
            node = parse_prefix(raw)
            self.assertEqual(node.kind, "complete")
            self.assertTrue(json_equal(node.value, expected))
        for raw in ['"unfinished', '"escape\\', '"unicode\\u12']:
            self.assertEqual(parse_prefix(raw).kind, "missing")

    def test_unicode_and_escaped_text_at_every_character(self):
        schema = obj({"text": {"type": "string"}, "done": {"type": "boolean"}})
        value = {"text": 'café 😺 "quote" \\ newline\n العربية', "done": True}
        for ensure_ascii in [False, True]:
            text = json.dumps(value, ensure_ascii=ensure_ascii, separators=(",", ":"))
            for cut in range(len(text) + 1):
                with self.subTest(ensure_ascii=ensure_ascii, cut=cut):
                    plan = compile_plan(schema, parse_prefix(text[:cut]))
                    self.assertTrue(json_equal(plan.merge(plan.project(value)), value))

    def test_malformed_prefixes_rejected(self):
        bad = ['{"a":1,"a":', '{"a":1,}', '[1,]', '[1,,', '{"a" 1', '{a:', '01', '1e+x', 'truex', '{"a": truX', '"bad\\x', '"line\n', '{}x', '[] {}', 'NaN', 'Infinity', '"\\uD800"']
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises((PrefixError, ValueError)):
                parse_prefix(raw)

    def test_invalid_or_infeasible_retained_values_rejected(self):
        schema = obj({"a": {"type": "integer", "enum": [1]}})
        for raw in ['{"a":2,', '{"extra":1,', '{"a":1,', '{"a":1,"partial', '{}', '{"a":true}']:
            with self.subTest(raw=raw), self.assertRaises((InfeasiblePrefix, ValueError)):
                compile_plan(schema, parse_prefix(raw))
        bounded = obj({"a": {"type": "array", "items": {"type": "integer"}, "maxItems": 1}})
        with self.assertRaises((InfeasiblePrefix, ValueError)):
            compile_plan(bounded, parse_prefix('{"a":[1,'))

    def test_unsupported_constraints_do_not_silently_disappear(self):
        samples = [
            {"type": "array", "items": {"type": "integer"}, "uniqueItems": True},
            {"type": "array", "items": {"type": "integer"}, "contains": {"type": "integer"}},
            {"oneOf": [{"type": "string"}, {"type": "integer"}]},
            {"allOf": [{"type": "string"}]},
            {"type": ["string", "null"]},
            {"type": "object", "properties": {"a": {"type": "string"}}, "additionalProperties": False},
            {"anyOf": [{"type": "string"}], "minLength": 2},
        ]
        for schema in samples:
            with self.subTest(schema=schema), self.assertRaises((UnsupportedSchema, ValueError)):
                compile_plan(schema, parse_prefix(""))

    def test_primitive_assertions_survive_residualization(self):
        schema = obj({"fixed": {"type": "string", "enum": ["kept"]}, "remaining": {"type": "string", "minLength": 2, "maxLength": 4, "pattern": "^[ab]+$"}})
        plan = compile_plan(schema, parse_prefix('{"fixed":"kept",'))
        for text in ["", "a", "ab", "aaaa", "aaaaa", "cc"]:
            full = {"fixed": "kept", "remaining": text}
            if Draft202012Validator(schema).is_valid(full):
                self.assertEqual(plan.merge(plan.project(full)), full)
            else:
                with self.assertRaises((InvalidContinuation, ValueError)):
                    plan.project(full)

    def test_repeated_interruption_composes_without_replacing_retained_values(self):
        _, schema, _, seeds = list(language_cases())[2]
        expected = seeds[0]
        original = encode(expected)
        first = compile_plan(schema, parse_prefix(original[:original.index('"active"') + 3]))
        first_payload = first.project(expected)
        wire = encode(first_payload)
        for cut in [0, 1, len(wire) // 2, len(wire) - 1, len(wire)]:
            second = compile_plan(first.schema, parse_prefix(wire[:cut]))
            recovered_payload = second.merge(second.project(first_payload))
            self.assertTrue(json_equal(first.merge(recovered_payload), expected))

    def test_source_and_payload_inputs_are_not_mutated(self):
        _, schema, _, seeds = list(language_cases())[2]
        schema_before = copy.deepcopy(schema)
        expected = copy.deepcopy(seeds[0])
        plan = compile_plan(schema, parse_prefix('{"version":1,"rows":['))
        payload = plan.project(expected)
        before = copy.deepcopy(payload)
        plan.merge(payload)
        self.assertEqual(schema, schema_before)
        self.assertEqual(payload, before)
        self.assertEqual(expected, seeds[0])

    def test_resource_limits_and_non_json_values_fail_closed(self):
        with self.assertRaises((PrefixError, ValueError)):
            parse_prefix('"abcd"', max_chars=3)
        with self.assertRaises((PrefixError, ValueError)):
            parse_prefix('[[[0]]]', max_depth=1)
        with self.assertRaises((PrefixError, ValueError)):
            parse_prefix('[0,1,2]', max_nodes=2)
        for keyword in ["max_chars", "max_depth", "max_nodes"]:
            with self.subTest(keyword=keyword), self.assertRaises(ValueError):
                parse_prefix("", **{keyword: 0})
        schema = {"type": "string"}
        plan = compile_plan(schema, parse_prefix(""))
        for bad in [float("nan"), float("inf"), {1: "not a JSON key"}, ("tuple",), "x" * 1_000_001]:
            with self.subTest(kind=type(bad).__name__), self.assertRaises((InvalidContinuation, ValueError)):
                plan.merge({"resume": bad})
        cyclic = {}
        cyclic["cycle"] = cyclic
        with self.assertRaises((InvalidContinuation, ValueError)):
            plan.merge({"resume": cyclic})
        deep = {"type": "string"}
        for _ in range(60):
            deep = obj({"child": deep})
        with self.assertRaises((UnsupportedSchema, ValueError)):
            compile_plan(deep, parse_prefix(""))


if __name__ == "__main__":
    import argparse
    import sys

    options = argparse.ArgumentParser(add_help=False)
    options.add_argument("--metrics-output", type=Path)
    args, remaining = options.parse_known_args()
    if args.metrics_output and args.metrics_output.exists():
        args.metrics_output.unlink()
    run = unittest.main(argv=[sys.argv[0], *remaining], verbosity=2, exit=False)
    if run.result.wasSuccessful() and args.metrics_output:
        args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
        args.metrics_output.write_text(json.dumps({"tests_run": run.result.testsRun, "scope": "Bounded finite universes plus mutations, not an exhaustive proof or measured model success rate.", **METRICS}, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if run.result.wasSuccessful() else 1)
