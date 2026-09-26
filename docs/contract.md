# Continuation contract

## Structural specialization

Let S be a supported schema and P the retained structure from a contiguous JSON prefix. Compilation produces a continuation schema R and reconstruction operation A. For values and schemas within the parser, compiler, and payload resource limits below, the intended contract is:

- Every payload accepted by R reconstructs to a value accepted by S that preserves P.
- Every S-valid value preserving P has a payload under R, computed by `project`.

This is a contract over retained **values and container structure**, not the literal remaining byte suffix or the interrupted model's hidden state. The finite tests provide executable evidence for it; they are not a general formal proof.

The implementation follows the structure of the input. A missing value retains its schema. A completed value is checked and removed from the generated payload. An open object retains complete children while deriving schemas for unfinished or unseen properties. An open array retains its complete prefix, separately derives its active item if any, and subtracts consumed positions from its tail bounds. An `anyOf` is specialized as whole alternatives; merging allowed values independently by field would destroy correlations. Local acyclic references are expanded before derivation.

## Parsing and rollback

The input must be a contiguous, correctly ordered JSON prefix from one response. The parser rejects malformed syntax, duplicate keys, comments, non-finite numbers, and non-portable completed strings with lone surrogates. It never invents missing primitive content.

- A closed string or container is retained.
- Numbers and true/false/null require a following JSON delimiter to become retained values. At end of input they are conservatively regenerated.
- An unfinished string, number, or literal is regenerated in full.
- An unfinished property name is discarded; a future unobserved key remains required.
- A trailing array comma requires a future item; a trailing object comma requires a future unobserved key. These are not silently converted into closing brackets.

Literal lexical equivalence is not promised: insignificant whitespace, object order, and numeric spelling can change. Sealed values are copied from the plan's immutable snapshot. Plan construction and `schema` access copy their inputs; mutating the caller's schema or prefix afterward does not change validation.

## Schema subset

The local validator uses JSON Schema Draft 2020-12. Supported primitive assertions are scalar `enum`/`const`; `minLength`, `maxLength`, `pattern` for strings; and numeric minimum/maximum, exclusive bounds, and `multipleOf`. `const` is normalized to a singleton enum. `type` is a single literal; nullable types use standalone `anyOf`.

Object properties must all be required and closed. Arrays use one homogeneous `items` schema. `$ref` points to local `$defs`; recursive, external, or assertion-sibling references are rejected. Annotations do not impose constraints. Unsupported assertion keywords, including unused definitions containing them, are rejected rather than dropped.

The compiler detects incompatible retained values, branches excluded by retained data, array overruns, and impossible next-key obligations. It does not solve general regular-expression or numeric satisfiability. A valid schema or derived schema can therefore have an empty language. Treat schemas as developer-owned inputs; regex validation is not a sandbox or a runtime-bounded operation.

The parser defaults to at most 1,000,000 characters, 64 nesting levels, and 100,000 nodes. The compiler and JSON payload checks use separate conservative size/depth/node bounds, documented as constants in `compiler.py`. Exceeding those limits raises an error. These local limits are not provider limits.

## Hosted APIs

The continuation has a closed object at its root, with a `resume` property. Provider acceptance depends on that provider's current JSON Schema subset, model, depth and size limits, and regex semantics. Wrappers and branch expansion can exceed limits even when the original schema fits.

The optional `check_openai_profile` helper checks the documented profile conservatively. It is a static report, not an API round trip, a provider endorsement, or evidence of native enforcement. v0.1 has no live-provider acceptance result. A consumer must not remove failing constraints while continuing to advertise the original contract.

The application supplies the original task, relevant retained context, termination reason, and retry budget. It must distinguish refusals from interruptions. residualgen neither executes tools nor manages model calls. After a second interruption, compile another plan against the first plan's schema, recover that payload, then merge through the earlier plan; wrappers and context costs accumulate.

## Meaning beyond the schema

The compiler cannot recover a model's intent or prove factual/copywriting quality. Preservation can freeze a semantic mistake. Schema-valid empty tails are possible where the application did not require more items. If the task demands nine scenes or one translation per input paragraph, the application must express and verify that obligation independently.
