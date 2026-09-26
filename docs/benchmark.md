# What was measured

**Deterministic UTF-8 serialization and reconstruction, not LLM performance.** Six authored fixtures are encoded through five recovery strategies. Their known completions are projected into method-specific payloads. Every reconstructed value must equal that fixture, pass its original schema, and preserve the retained prefix. These are oracle checks, not generated success rates.

Reproduce from the repository root:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -v
python test_contract.py --metrics-output results/contract-metrics.json
python benchmark.py --output results/benchmark.json
```

The [complete JSON report](../results/benchmark.json) includes every schema, original value, raw cut prefix, method response, byte count, and fixture hash. [Contract metrics](../results/contract-metrics.json) record 10,030 finite candidate checks across 323 prefixes and 48,127 mutation operations. Repeated payloads at different prefixes count separately; these are not independent samples or confidence intervals. The full suite also covers parser retention, reference decoding, mutation isolation, resource limits, and the static provider profile.

## Representative late interruption

An authored campaign contains four audience variants, each requiring three scenes. The stream is cut inside a string in the fourth variant's second scene. The unfinished string is regenerated completely; prior complete variants and scene values remain available.

| Strategy | Response bytes | Modeled request + response bytes |
|---|---:|---:|
| Full restart | 4,448 | **5,519** |
| Object pruning, interrupted arrays retried whole | 4,297 | 9,083 |
| Validated JSON Patch | 968 | 6,069 |
| Oracle-assisted known slots | **719** | 5,802 |
| Compiled continuation | 837 | 5,933 |

The compiled response is **80.5% smaller than the atomic-array retry** and **13.5% smaller than the selected JSON Patch encoding** on this fixture. It is 81.2% smaller than a full regenerated response, but its modeled request plus response is **7.5% larger than restarting**. Retained context and the continuation schema have a cost.

The known-slot strategy has a smaller response than the compiler. It is deliberately favorable: it receives oracle-known missing paths and emits only their values, with final validation. An equivalent handwritten typed continuation can also reproduce the compiler's contract. This project offers reusable automatic specialization; it does not claim to outperform equivalent application-specific code.

At an illustrative 1,024-byte response budget, the authored oracle encodings for Patch, known slots, and the compiler fit this fixture; full and atomic-array regeneration do not. This is a byte-budget illustration, **not a provider token-window experiment**.

## Negative and contextual cases

| Fixture | Restart response → compiled response | Restart total → compiled total |
|---|---:|---:|
| Campaign, interrupted in first key | 4,448 → 4,459 | 5,519 → 5,628 |
| Campaign, fourth variant just opened | 4,448 → 1,186 | 5,519 → 5,618 |
| Campaign, partial scene above | 4,448 → 837 | 5,519 → 5,933 |
| Tiny object | 26 → 22 | 171 → 240 |
| One huge unfinished string | 9,092 → 9,103 | 9,242 → 16,594 |
| Video-script-shaped collection, late cut | 1,983 → 242 | 2,378 → 2,856 |

Every fixture loses to restart on the modeled total bytes in this suite. Early cuts and a large unfinished string provide essentially no response advantage. The video case uses independently authored values in a source-inspired shape; it does not reproduce the reported upstream provider failure. Its unbounded lists also permit a schema-valid empty tail, so structural completion does not prove that all intended scenes were written.

## Baseline definitions and accounting

- **Full restart:** regenerate the complete fixture, with no prior output retransmitted. It receives correctness credit, including matching retained values; this is an oracle assumption, not a claim that models always match them.
- **Selective subtree retry:** recursively preserve completed object siblings, but retry an interrupted array or union subtree as a whole. Final schema and prefix checks are credited.
- **JSON Patch:** choose between preserving missing-subtree add operations and replacing allowed ancestors, using the known fixture to select a shorter response among the implemented candidates. This is favorable oracle selection, not a globally byte-optimal patch claim. It receives full correctness credit after reconstruction, original-schema validation, and prefix checks.
- **Known slots:** oracle-known paths move addressing out of the response, which contains only values. These paths are not discovered by a measured model. Its untyped response schema relies on final validation.
- **Compiled continuation:** use `plan.project` to derive the fixture payload and `plan.merge` to verify it. The generated schema restricts unfinished values before generation; the actual model/provider enforcement of that schema has not been tested.

Sizes use compact JSON with `ensure_ascii=False`, encoded in UTF-8. Modeled requests include the output schema, raw retained prefix where used, and explicit metadata such as original schema or slot paths. Original task/system prompts, method-specific natural-language instructions, HTTP envelopes, and SDK overhead are excluded. Schema caching, inference work, tokenization, and billed tokens are not modeled. None of these figures is a price or latency measurement. Alternative context and prompting strategies can change the totals.

All six compiled fixtures pass the conservative static documented-provider-profile check. That is not a live API acceptance test. Benchmarks compare independently implemented encoding strategies, not the measured runtime performance of Guardrails, Parsantic, Trustcall, Instructor, or nfield.

## Why the contribution remains useful

The demonstrated benefit is retaining completed work while automatically compiling a constrained request for nested unfinished data. On suitable late cuts, that request has a substantially smaller response. Correlated unions and array bounds remain enforced by the resulting schema within the supported fragment. The value is strongest where avoiding repeated generated output matters and the application accepts the extra request context and schema work. Quality, actual token economics, live-provider compatibility, and workload prevalence remain open measurements.
