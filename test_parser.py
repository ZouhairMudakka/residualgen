"""Independent JSON syntax and every-cut prefix checks."""

import json
import random
import unittest

from residualgen.parser import PrefixError, parse_prefix


class ParserTests(unittest.TestCase):
    def test_every_cut_of_standard_library_serialized_values(self):
        rng = random.Random(90427)
        leaves = [None, True, False, 0, -1, 1203, -1.25e-9, "", 'quote"slash\\', "line\nnext", "🙂", "عربي"]

        def build(depth):
            if depth == 0 or rng.random() < 0.5:
                return rng.choice(leaves)
            if rng.random() < 0.5:
                return [build(depth - 1) for _ in range(rng.randrange(4))]
            return {f"k{i}": build(depth - 1) for i in range(rng.randrange(4))}

        for _ in range(150):
            value = {"data": build(4)}
            for ascii_only in (True, False):
                raw = json.dumps(value, ensure_ascii=ascii_only, separators=(",", ":"))
                for stop in range(len(raw) + 1):
                    with self.subTest(raw=raw, stop=stop):
                        parse_prefix(raw[:stop])
                node = parse_prefix(raw)
                self.assertEqual(node.kind, "complete")
                self.assertEqual(node.value, value)

    def test_rejects_non_prefix_syntax(self):
        bad = ['{"a":01', '{"a":1.e', '{"a":1e--', '{"a":-e', '{"a":NaN',
               '{"a":truX', '{"a":truex', '{"a":1 "b":2}', '{"a":1,}',
               '[1,]', '[,', '{1:', '/*', '{"a":"\\q', '{"a":"\\u00zz',
               '{"a":"x\nx', '{"a":1,"a"', '{}x', '{"a":Infinity}',
               '{"a":"\\ud800"}', '{"a":1e999}']
        for raw in bad:
            with self.subTest(raw=raw):
                with self.assertRaises(PrefixError):
                    parse_prefix(raw)

    def test_never_commits_incomplete_primitive(self):
        for raw in ['{"a":1', '{"a":true', '{"a":null', '{"a":"hel', '{"a":1e-']:
            self.assertEqual(parse_prefix(raw).value["a"].kind, "missing")
        self.assertEqual(parse_prefix('{"a":1 ').value["a"].value, 1)
        self.assertEqual(parse_prefix('{"a":"hello"').value["a"].value, "hello")

    def test_explicit_next_value_obligations(self):
        self.assertTrue(parse_prefix('{"a":1,').needs_key)
        self.assertTrue(parse_prefix('{"a":1,"b').needs_key)
        self.assertFalse(parse_prefix('{"a":1 ').needs_key)
        arr = parse_prefix('[1,')
        self.assertEqual([n.kind for n in arr.value], ["complete", "missing"])
        self.assertEqual(parse_prefix('[').value, ())

    def test_resource_limits(self):
        with self.assertRaises(PrefixError):
            parse_prefix('[' * 20, max_depth=10)
        with self.assertRaises(PrefixError):
            parse_prefix('[0,0,0]', max_nodes=2)
        with self.assertRaises(PrefixError):
            parse_prefix('{}', max_chars=1)

    def test_completed_nested_values_survive_an_open_ancestor(self):
        node = parse_prefix('{"a":[{"x":"one"}],"b":{"k":2,')
        self.assertEqual(node.kind, "object")
        self.assertEqual(node.value["a"].kind, "complete")
        self.assertEqual(node.value["a"].value, [{"x": "one"}])
        self.assertEqual(node.value["b"].kind, "object")
        self.assertEqual(node.value["b"].value["k"].kind, "complete")
        self.assertEqual(node.value["b"].value["k"].value, 2)
        self.assertTrue(node.value["b"].needs_key)

        node = parse_prefix('[{"a":[1,2]},[true,null],{"k":"yes","next":"par')
        self.assertEqual(node.kind, "array")
        self.assertEqual(node.value[0].value, {"a": [1, 2]})
        self.assertEqual(node.value[1].value, [True, None])
        self.assertEqual(node.value[2].kind, "object")
        self.assertEqual(node.value[2].value["k"].value, "yes")
        self.assertEqual(node.value[2].value["next"].kind, "missing")


if __name__ == "__main__":
    unittest.main()
