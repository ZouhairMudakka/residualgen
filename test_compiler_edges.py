"""Focused compiler boundaries; no model/provider execution."""
import unittest

from residualgen import Node, compile_plan, parse_prefix
from residualgen.compiler import InfeasiblePrefix, InvalidContinuation, UnsupportedSchema


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


class CompilerEdges(unittest.TestCase):
    def test_uri_fragment_decode_order_and_exactly_once(self):
        for name, ref, expected in (
            ("a b", "#/$defs/a%20b", 1),
            ("a%20b", "#/$defs/a%2520b", 2),
            ("a/b", "#/$defs/a~1b", 3),
            ("a~b", "#/$defs/a~0b", 4),
            ("café", "#/$defs/caf%C3%A9", 5),
        ):
            with self.subTest(ref=ref):
                source = obj({"v": {"$ref": ref}})
                source["$defs"] = {name: {"type": "integer", "enum": [expected]}}
                if name == "a b":
                    source["$defs"]["a%20b"] = {"type": "integer", "enum": [999]}
                plan = compile_plan(source, parse_prefix('{"v":'))
                payload = plan.project({"v": expected})
                self.assertEqual(plan.merge(payload), {"v": expected})
                with self.assertRaises(InvalidContinuation):
                    plan.merge({"resume": {"v": 999}})

    def test_boolean_numeric_branch_dedup_does_not_erase_valid_branch(self):
        # The first branch is empty: true does not satisfy type:number. Python
        # dictionary equality alone would collapse it with enum:[1].
        source = obj({"v": {"anyOf": [obj({"n": {"type": "number", "enum": [True]}}), obj({"n": {"type": "number", "enum": [1]}})]}})
        plan = compile_plan(source, parse_prefix('{"v":{'))
        self.assertEqual(plan.merge(plan.project({"v": {"n": 1}})), {"v": {"n": 1}})
        with self.assertRaises(InvalidContinuation):
            plan.project({"v": {"n": True}})

    def test_definitions_and_keywords_fail_closed(self):
        for extension in ({"not": {}}, {"format": "custom"}, {"dependentRequired": {}}, {"x-unknown-assertion": True}):
            source = obj({"v": {"type": "string", **extension}})
            with self.subTest(extension=extension), self.assertRaises(UnsupportedSchema):
                compile_plan(source, parse_prefix("{"))
        source = obj({})
        source["$defs"] = {"unused": {"type": "string", "not": {}}}
        with self.assertRaises(UnsupportedSchema):
            compile_plan(source, parse_prefix("{"))
        source["$defs"] = {"loop": {"$ref": "#/$defs/loop"}}
        with self.assertRaises(UnsupportedSchema):
            compile_plan(source, parse_prefix("{"))
        for ref in ("https://example.invalid/schema", "#/$defs/a%ZZ", "#/$defs/a~2b"):
            source = obj({"v": {"$ref": ref}})
            with self.subTest(ref=ref), self.assertRaises(UnsupportedSchema):
                compile_plan(source, parse_prefix("{"))

    def test_caller_mutation_cannot_change_retained_values_or_schema(self):
        source = obj({"a": {"type": "array", "items": {"type": "integer"}}, "b": {"type": "integer", "enum": [2]}})
        retained = [1]
        frontier = Node("object", {"a": Node("complete", retained)})
        plan = compile_plan(source, frontier)
        retained.append(999)
        source["properties"]["b"]["enum"] = [999]
        exported_schema = plan.schema
        exported_schema["properties"]["resume"]["properties"]["b"]["enum"] = [999]
        self.assertEqual(plan.merge({"resume": {"b": 2}}), {"a": [1], "b": 2})
        with self.assertRaises(InvalidContinuation):
            plan.merge({"resume": {"b": 999}})
        with self.assertRaises(InvalidContinuation):
            plan.project({"a": [True], "b": 2})

    def test_trailing_new_key_is_infeasible_when_all_keys_seen(self):
        with self.assertRaises(InfeasiblePrefix):
            compile_plan(obj({"a": {"type": "integer"}}), parse_prefix('{"a":1,'))

    def test_zero_capacity_tail_does_not_repeat_unreachable_item_schema(self):
        source = obj({"rows": {"type": "array", "items": obj({"id": {"type": "integer"}, "name": {"type": "string"}}), "minItems": 1, "maxItems": 1}})
        plan = compile_plan(source, parse_prefix('{"rows":[{"id":1,'))
        tail = plan.schema["properties"]["resume"]["properties"]["rows"]["properties"]["tail"]
        self.assertEqual(tail, {"type": "array", "items": {"type": "null"}, "minItems": 0, "maxItems": 0})
        self.assertEqual(plan.merge({"resume": {"rows": {"item": {"name": "N"}, "tail": []}}}), {"rows": [{"id": 1, "name": "N"}]})
        with self.assertRaises(InvalidContinuation):
            plan.merge({"resume": {"rows": {"item": {"name": "N"}, "tail": [None]}}})


if __name__ == "__main__":
    unittest.main()
