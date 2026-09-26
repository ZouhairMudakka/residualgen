# residualgen: resume interrupted structured LLM output

[![CI](https://github.com/ZouhairMudakka/residualgen/actions/workflows/ci.yml/badge.svg)](https://github.com/ZouhairMudakka/residualgen/actions/workflows/ci.yml)

Turn an interrupted JSON generation into a **new JSON Schema for the unfinished data**, then reconstruct the original object without asking the model to repeat completed values.

For developers generating agent plans, campaign variants, video scenes, or other nested structured artifacts. This is a small Python compiler, with a strict prefix parser and reconstruction API. It does not call a model or execute tools.

## What it adds

Given an original schema and a contiguous interrupted response, residualgen:

1. Retains completed values and rolls back unfinished primitive values.
2. Separates a partially generated array item from the remaining array tail, adjusting the remaining length bounds.
3. Specializes complete `anyOf` branches against retained values, preserving correlations between unfinished fields.
4. Emits a fresh, self-contained JSON response schema and validates reconstruction against the original schema.

The contribution is the automatic combination of nested-frontier specialization, correlated branch preservation, and reconstruction for a documented schema subset. It uses established parsing, partial evaluation, schema validation, and selective regeneration ideas. We make **no first-in-the-world claim**. See [prior art](docs/prior-art.md) and the [contract](docs/contract.md).

## Try it

```bash
git clone https://github.com/ZouhairMudakka/residualgen.git
cd residualgen
python -m pip install -r requirements.txt
python demo.py
python -m unittest discover -v
```

The demo uses clearly labeled authored fixtures. It cuts a campaign artifact inside its third deliverable, derives a continuation schema, and reconstructs the complete fixture. **It is not a model performance test.**

Optional installation for the `residualgen` command:

```bash
python -m pip install -e .
```

## Integrate with generation

```python
from residualgen import compile_plan, parse_prefix

plan = compile_plan(original_schema, parse_prefix(interrupted_text))

# Your application makes a new model request using plan.schema, the original
# task/context, and the retained response as context. This package makes no call.
reply = your_generate_function(schema=plan.schema, context=original_context)
complete_value = plan.merge(reply)  # fresh Python JSON value, fully validated
```

`your_generate_function` is application code, not a bundled provider adapter. Its result is a parsed continuation object of the form `{"resume": ...}`. The model may need the retained values to preserve semantic coherence; include them in its context rather than assuming they are implicit in the schema.

For a reproducible fixture only, `plan.project(known_complete_value)` computes the expected continuation payload. Do not count that oracle operation as successful generation.

The CLI works with files and makes no network request:

```bash
python -m residualgen plan schema.json interrupted.txt --out continuation.schema.json
python -m residualgen merge schema.json interrupted.txt --reply continuation.json --out recovered.json
```

For existing files you can run immediately, use the [authored round-trip example](examples/README.md).

## Supported contract

- Closed objects: every declared property required, `additionalProperties: false`.
- Homogeneous arrays, including `minItems` / `maxItems`.
- Primitive types, scalar enums/constants, and the implemented primitive bounds/patterns.
- Nested `anyOf` without assertion siblings; local acyclic `$defs` references.
- Unknown or unsupported assertions are rejected. No silent constraint removal.

The package does not support arbitrary JSON Schema. In particular, optional/open object properties, `oneOf`, `allOf`, `not`, `contains`, `uniqueItems`, dependency/conditional rules, recursive references, and `format` are outside v0.1. Nullable values can use `anyOf`. See [contract details](docs/contract.md) for lexical rollback, resource limits, and provider compatibility.

## Evidence and limits

The test suite checks finite completion universes against an independent JSON Schema validator, every-character truncations, mutated continuation payloads, branch correlation, repeated interruptions, immutability, references, and malformed prefixes. These are bounded executable checks, not a machine-checked proof of every possible schema.

The benchmark compares serialized response and request-contract sizes against full restart, selective subtree retry, JSON Patch, and an oracle-assisted known-slot strategy. On one authored late-cut campaign fixture, the response falls from **4,297 to 837 bytes (80.5%)** versus atomic-array retry, and from **968 to 837 bytes (13.5%)** versus the selected validated JSON Patch encoding. Total request-plus-response bytes are **7.5% larger than restarting** on that fixture.

An equivalent handwritten typed continuation can match the compiler; it is an acknowledged limit, not a measured superiority claim. All fixtures, negative cases, and accounting are in [the benchmark report](docs/benchmark.md).

No live hosted-provider acceptance, model quality, token bill, latency, production failure rate, or revenue improvement has been measured. A static documented-provider-profile check is useful preparation, but does not establish API acceptance.

```python
from residualgen.profile import check_openai_profile
issues = check_openai_profile(plan.schema)
# An empty list means the static documented-profile check passed, not that a
# provider has accepted or enforced the schema in a live request.
```

Schema validity does not prove task completeness. An unconstrained scene list can legally stop early; an already completed paragraph can be wrong. A huge unfinished string offers little reusable work. Early interruptions and small payloads may cost more to recover because continuation schemas and context add overhead. Raising the output limit or generating deliberate chunks may be the better application design.

Use recovery for interrupted generation, not as a way to bypass a refusal. Partial tool arguments must never be executed. The original request, model state, and stream transport are not resumed by this library.

## Attribution

Created by **Zouhair Mudakka**, with AI-assisted implementation and review. MIT licensed. The runtime uses the independently maintained `jsonschema` package. No implementation code from the compared projects is vendored. [Prior-art and source attribution](docs/prior-art.md) distinguish established techniques from this implementation's contribution.
