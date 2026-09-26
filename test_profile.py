"""Boundary tests for a static documented profile, not API acceptance tests."""

import unittest

from residualgen.profile import check_openai_profile


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


class ProfileTests(unittest.TestCase):
    def test_normal_residual_shape_and_nested_union(self):
        schema = obj({"resume": {"anyOf": [obj({"item": {"type": "string", "enum": ["a"]}, "tail": {"type": "array", "items": {"type": "integer", "minimum": 0}, "maxItems": 3}}), obj({"done": {"type": "boolean"}})]}})
        self.assertEqual(check_openai_profile(schema), [])

    def test_root_closure_required_and_unknown_assertions(self):
        for schema in [
            {"anyOf": [obj({}), obj({"a": {"type": "string"}})]},
            {"type": "object", "properties": {}, "required": []},
            obj({"a": {"type": "string", "minLength": 1}}),
            obj({"a": {"type": "array", "items": {"type": "integer"}, "uniqueItems": True}}),
            obj({"a": {"$ref": "#/$defs/a"}}),
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": [], "additionalProperties": False},
        ]:
            with self.subTest(schema=schema):
                self.assertTrue(check_openai_profile(schema))

    def test_property_count_boundary(self):
        self.assertEqual(check_openai_profile(obj({f"p{i}": {"type": "null"} for i in range(5_000)})), [])
        self.assertTrue(any("5,000" in error for error in check_openai_profile(obj({f"p{i}": {"type": "null"} for i in range(5_001)}))))

    def test_nesting_counts_arrays_and_objects_but_not_union(self):
        inner = {"type": "null"}
        for _ in range(9):
            inner = {"type": "array", "items": inner}
        self.assertEqual(check_openai_profile(obj({"a": {"anyOf": [inner, {"type": "null"}]}})), [])
        self.assertTrue(any("Nesting" in error for error in check_openai_profile(obj({"a": {"type": "array", "items": inner}}))))

    def test_enum_count_and_string_boundaries(self):
        self.assertEqual(check_openai_profile(obj({"a": {"type": "integer", "enum": list(range(1_000))}})), [])
        self.assertTrue(any("1,000" in error for error in check_openai_profile(obj({"a": {"type": "integer", "enum": list(range(1_001))}}))))
        self.assertEqual(check_openai_profile(obj({"a" * 120_000: {"type": "null"}})), [])
        self.assertTrue(any("120,000" in error for error in check_openai_profile(obj({"a" * 120_001: {"type": "null"}}))))
        values = [str(i).zfill(60) for i in range(251)]
        self.assertTrue(any("15,000" in error for error in check_openai_profile(obj({"a": {"type": "string", "enum": values}}))))

    def test_annotation_text_ignored_and_invalid_bounds_rejected(self):
        schema = obj({"a": {"type": "string", "pattern": "^[ab]+$", "description": "x" * 120_001}})
        self.assertEqual(check_openai_profile(schema), [])
        for bounds in [{"minItems": True}, {"maxItems": -1}, {"minItems": 2, "maxItems": 1}]:
            self.assertTrue(check_openai_profile(obj({"a": {"type": "array", "items": {"type": "null"}, **bounds}})))

    def test_cyclic_input_fails_with_diagnostic(self):
        schema = obj({})
        schema["properties"]["loop"] = schema
        schema["required"].append("loop")
        self.assertTrue(any("Cyclic" in error for error in check_openai_profile(schema)))


if __name__ == "__main__":
    unittest.main()
