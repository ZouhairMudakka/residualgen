"""Deterministic recovery-wire benchmark, with no model calls or generated-quality claim.

All completions are authored oracle values. Every baseline must independently
reconstruct that value, preserve the retained prefix and pass the source schema.
UTF-8 JSON bytes are not tokens, price, latency, or probability of model success.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from residualgen import compile_plan, parse_prefix
from residualgen.parser import Node
from residualgen.profile import check_openai_profile
from test_contract import encode, json_equal, obj, preserves


def require(condition, message):
    if not condition:
        raise ValueError(message)


def size(value):
    return len(encode(value).encode("utf-8"))


def object_retry(schema, node, full):
    """Competent object pruning; an interrupted array/union is atomic.

    This baseline retains completed object siblings at every ordinary object
    depth. It reissues a whole interrupted array or union subtree, then checks
    preservation independently; it is not a deliberately naive root restart.
    """
    if node.kind == "complete":
        return obj({}), {}, lambda payload: copy.deepcopy(node.value)
    if node.kind != "object" or schema.get("type") != "object":
        return copy.deepcopy(schema), copy.deepcopy(full), copy.deepcopy
    properties, payload, mergers = {}, {}, {}
    for key, child_schema in schema["properties"].items():
        child = node.value.get(key, Node("missing"))
        if child.kind != "complete":
            properties[key], payload[key], mergers[key] = object_retry(child_schema, child, full[key])

    def merge(value):
        result = {key: copy.deepcopy(child.value) for key, child in node.value.items() if child.kind == "complete"}
        result.update({key: mergers[key](item) for key, item in value.items()})
        return result

    return obj(properties), payload, merge


_MISSING = object()


def retained_document(node):
    """Concrete partial document for a standard add-only JSON Patch baseline."""
    if node.kind == "complete":
        return copy.deepcopy(node.value)
    if node.kind == "missing":
        return _MISSING
    if node.kind == "object":
        result = {}
        for key, child in node.value.items():
            value = retained_document(child)
            if value is not _MISSING:
                result[key] = value
        return result
    result = []
    for child in node.value:
        value = retained_document(child)
        if value is not _MISSING:
            result.append(value)
    return result


def pointer(path):
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in path)


def frontier_adds(node, full, path=()):
    """One add for each wholly missing subtree; recurse only into open ones."""
    if node.kind == "complete":
        return []
    if node.kind == "missing":
        return [{"op": "add", "path": pointer(path), "value": copy.deepcopy(full)}]
    result = []
    if node.kind == "object":
        for key, value in full.items():
            result.extend(frontier_adds(node.value.get(key, Node("missing")), value, path + (key,)))
    else:
        for index, value in enumerate(full):
            child = node.value[index] if index < len(node.value) else Node("missing")
            result.extend(frontier_adds(child, value, path + (index,)))
    return result


def byte_selected_patch(node, full, path=()):
    """Choose between frontier edits and each ancestor replacement using oracle values.

    This favors the baseline: it may repeat sealed values unchanged when doing
    so saves JSON Patch path overhead. It is not a globally optimal patch solver
    (move/copy, arbitrary operation order and alternative encodings are absent).
    """
    if node.kind == "complete":
        return []
    if node.kind == "missing":
        return [{"op": "add", "path": pointer(path), "value": copy.deepcopy(full)}]
    children = []
    if node.kind == "object":
        for key, value in full.items():
            children.extend(byte_selected_patch(node.value.get(key, Node("missing")), value, path + (key,)))
    else:
        for index, value in enumerate(full):
            child = node.value[index] if index < len(node.value) else Node("missing")
            children.extend(byte_selected_patch(child, value, path + (index,)))
    replacement = [{"op": "replace", "path": pointer(path), "value": copy.deepcopy(full)}]
    return min([children, replacement], key=size)


def apply_patch(document, operations):
    result = copy.deepcopy(document) if document is not _MISSING else _MISSING
    for operation in operations:
        require(operation["op"] in {"add", "replace"}, "Benchmark only produces standard add/replace operations")
        path, value = operation["path"], copy.deepcopy(operation["value"])
        if path == "":
            result = value
            continue
        require(path.startswith("/"), "Invalid JSON pointer")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]
        parent = result
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        if isinstance(parent, list):
            index = len(parent) if parts[-1] == "-" else int(parts[-1])
            if operation["op"] == "add":
                require(0 <= index <= len(parent), "Invalid JSON Patch add index")
                parent.insert(index, value)
            else:
                require(0 <= index < len(parent), "Invalid JSON Patch replace index")
                parent[index] = value
        else:
            require(operation["op"] == "add" or parts[-1] in parent, "JSON Patch replace target must exist")
            parent[parts[-1]] = value
    return result


PATCH_SCHEMA = obj({"patch": {"type": "array", "items": obj({
    "op": {"type": "string", "enum": ["add", "replace"]},
    "path": {"type": "string"},
    "value": {},
})}})
SLOTS_SCHEMA = obj({"values": {"type": "array", "items": {}}})


def authored_campaign():
    scene_schema = {"anyOf": [
        obj({"kind": {"type": "string", "enum": ["voiceover"]}, "narration": {"type": "string"}, "duration": {"type": "integer", "minimum": 1, "maximum": 30}}),
        obj({"kind": {"type": "string", "enum": ["visual"]}, "image_prompt": {"type": "string"}, "duration": {"type": "integer", "minimum": 1, "maximum": 30}}),
    ]}
    variant_schema = obj({
        "channel": {"type": "string", "enum": ["email", "video"]},
        "headline": {"type": "string"},
        "scenes": {"type": "array", "items": scene_schema, "minItems": 3, "maxItems": 3},
        "call_to_action": {"type": "string"},
    })
    schema = obj({"campaign_name": {"type": "string"}, "objective": {"type": "string"}, "variants": {"type": "array", "items": variant_schema, "minItems": 4, "maxItems": 4}, "review_note": {"type": "string"}})
    variants = []
    for index, audience in enumerate(["home cooks", "small cafés", "event caterers", "school kitchens"]):
        scenes = [
            {"kind": "voiceover", "narration": f"For {audience}, show how the weekly produce box fits an ordinary workday. Explain the delivery window, let the customer inspect the contents, and demonstrate one practical recipe without promising savings that have not been measured.", "duration": 18},
            {"kind": "visual", "image_prompt": f"Scene {index + 1}.2: eye-level photograph of a reusable produce crate on a clean preparation table for {audience}. Include seasonal vegetables with natural imperfections, soft window light, and readable empty space for delivery information. No logos or price claims.", "duration": 8},
            {"kind": "voiceover", "narration": f"End the {audience} story by explaining how to choose a box size and pause a delivery. State that contents vary with the season, show the available collection options, and invite viewers to check the current list before ordering.", "duration": 15},
        ]
        variants.append({"channel": "video" if index % 2 else "email", "headline": f"A practical produce routine for {audience}", "scenes": scenes, "call_to_action": "Check this week's contents and choose a collection slot."})
    full = {"campaign_name": "Seasonal produce, ordinary routines", "objective": "Explain the service through four audience-specific variants, each with exactly three scenes.", "variants": variants, "review_note": "An editor must check current availability, accessibility, and factual claims before publication."}
    return schema, full


def fixtures():
    schema, full = authored_campaign()
    raw = encode(full)
    last_variant = raw.index('"headline":"A practical produce routine for school kitchens"')
    partial_scene = raw.index("Scene 4.2:") + len("Scene 4.2: eye-level")
    for identifier, cut, reason in [
        ("campaign-early-key", 5, "No completed values; early interruption is a negative control."),
        ("campaign-last-variant", last_variant, "Three complete variants retained; fourth object is open."),
        ("campaign-last-variant-partial-scene", partial_scene, "An unfinished string rolls back; earlier scenes and variants remain usable."),
    ]:
        yield {"id": identifier, "schema": schema, "full": full, "prefix": raw[:cut], "origin": "authored fixed-count campaign task", "reason": reason}
    tiny_schema = obj({"answer": {"type": "string"}, "count": {"type": "integer"}})
    tiny_full = {"answer": "yes", "count": 1}
    yield {"id": "tiny-object", "schema": tiny_schema, "full": tiny_full, "prefix": '{"answer":"yes",', "origin": "authored negative control", "reason": "Protocol/schema overhead may exceed response savings."}
    prose = "Describe the real ingredients, collection window, and preparation steps clearly. Do not invent customer testimonials or unsupported price comparisons. " * 60
    prose_schema = obj({"document": {"type": "string"}, "status": {"type": "string"}})
    prose_full = {"document": prose, "status": "ready"}
    yield {"id": "large-unfinished-string", "schema": prose_schema, "full": prose_full, "prefix": encode(prose_full)[:int(len(prose) * 0.8)], "origin": "authored negative control", "reason": "Completed-value recovery cannot reuse a long unfinished string."}
    # Independently authored values in the public VideoScript/SceneScript shape.
    # The upstream List fields do not enforce an exact scene/narration count.
    scene = obj({"scene_number": {"type": "integer"}, "asset_path": {"type": "string"}, "narrations": {"type": "array", "items": {"type": "string"}}, "duration": {"type": "integer"}})
    video_schema = obj({"scenes": {"type": "array", "items": scene}})
    video_full = {"scenes": [{"scene_number": i + 1, "asset_path": f"assets/slide-{i + 1}.png", "narrations": [f"Slide {i + 1} introduces one concrete part of the service.", "Explain the visible steps and invite the viewer to verify current availability."], "duration": 15} for i in range(9)]}
    video_raw = encode(video_full)
    cut = video_raw.index('"asset_path":"assets/slide-9.png"')
    yield {"id": "video-script-shape-late", "schema": video_schema, "full": video_full, "prefix": video_raw[:cut], "origin": "authored values, source-inspired VideoScript shape", "source": "https://github.com/ATH-MaaS/Pixelle-Video/issues/141", "reason": "Matches a reported long scene-list truncation use case, but does not reproduce its provider or infer semantic completeness from an unbounded list."}


def method_record(name, schema, response, recovered, source_schema, node, full, prefix, extra=None, explanation=""):
    require(Draft202012Validator(schema).is_valid(response), f"{name}: oracle response violates method schema")
    require(Draft202012Validator(source_schema).is_valid(recovered), f"{name}: reconstructed value violates source schema")
    require(preserves(node, recovered), f"{name}: reconstructed value changes retained prefix")
    require(json_equal(recovered, full), f"{name}: reconstruction differs from the authored oracle")
    context_bytes = len(prefix.encode("utf-8"))
    extra_bytes = 0 if extra is None else size(extra)
    schema_bytes, response_bytes = size(schema), size(response)
    return {
        "method": name,
        "oracle_response_bytes": response_bytes,
        "fits_illustrative_1024_byte_response_budget": response_bytes <= 1024,
        "output_schema_bytes": schema_bytes,
        "retained_context_bytes": context_bytes,
        "extra_request_metadata_bytes": extra_bytes,
        "modeled_request_bytes": schema_bytes + context_bytes + extra_bytes,
        "modeled_request_plus_response_bytes": schema_bytes + context_bytes + extra_bytes + response_bytes,
        "validated_oracle_reconstruction": True,
        "explanation": explanation,
        "response": response,
        "output_schema": schema,
        "extra_request_metadata": extra,
    }


def evaluate_fixture(fixture):
    schema, full, prefix = fixture["schema"], fixture["full"], fixture["prefix"]
    node = parse_prefix(prefix)
    require(node.kind != "complete", "Complete outputs should be validated and returned without a retry")
    require(Draft202012Validator(schema).is_valid(full) and preserves(node, full), "Invalid authored fixture")
    rows = []
    rows.append(method_record("full_restart", schema, full, copy.deepcopy(full), schema, node, full, "", explanation="Credit successful full regeneration and final validation. No prior output is retransmitted in this modeled request."))
    retry_schema, retry_payload, retry_merge = object_retry(schema, node, full)
    rows.append(method_record("object_pruning_atomic_arrays", obj({"retry": retry_schema}), {"retry": retry_payload}, retry_merge(retry_payload), schema, node, full, prefix, explanation="Recursively prune complete object siblings. Retry interrupted arrays or unions atomically. Final source validation and prefix checks are credited."))
    additions = frontier_adds(node, full)
    partial = retained_document(node)
    selected_patch = byte_selected_patch(node, full)
    patch_payload = {"patch": selected_patch}
    rows.append(method_record("validated_json_patch", PATCH_SCHEMA, patch_payload, apply_patch(partial, selected_patch), schema, node, full, prefix, extra={"original_schema": schema}, explanation="Oracle byte-selected standard add/replace patch: compare frontier edits with whole-subtree replacement at each ancestor. This credits repeated sealed values when cheaper, is not a global optimum, and receives full correctness credit after final validation/prefix checks. The patch schema does not constrain individual values."))
    slot_paths = [operation["path"] for operation in additions]
    slot_payload = {"values": [operation["value"] for operation in additions]}
    handwritten_ops = [{"op": "add", "path": path, "value": value} for path, value in zip(slot_paths, slot_payload["values"])]
    rows.append(method_record("handwritten_known_slots", SLOTS_SCHEMA, slot_payload, apply_patch(partial, handwritten_ops), schema, node, full, prefix, extra={"known_slot_paths": slot_paths, "original_schema": schema}, explanation="Favorable application-specific baseline: correct missing slot paths are provided in advance, so output contains only values. Final validation and prefix checks are credited. Paths here are oracle-derived; discovering them is not benchmarked."))
    plan = compile_plan(schema, node)
    response = plan.project(full)
    rows.append(method_record("compiled_residual", plan.schema, response, plan.merge(response), schema, node, full, prefix, explanation="Fresh object response schema constrains the unfinished structure. This is an oracle projection, not a generated response or evidence of provider acceptance."))
    return {
        "id": fixture["id"], "origin": fixture["origin"], "reason": fixture["reason"],
        "source": fixture.get("source"), "full_json_bytes": size(full),
        "prefix_bytes": len(prefix.encode("utf-8")), "source_schema_bytes": size(schema),
        "source_schema": schema, "full_value": full, "raw_prefix": prefix,
        "full_sha256": hashlib.sha256(encode(full).encode("utf-8")).hexdigest(),
        "compiled_static_openai_profile_diagnostics": check_openai_profile(plan.schema),
        "static_profile_is_not_live_provider_acceptance": True,
        "methods": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "results" / "benchmark.json")
    args = parser.parse_args()
    if args.output.exists():
        args.output.unlink()  # This invocation must not leave a stale successful report.
    cases = [evaluate_fixture(fixture) for fixture in fixtures()]
    report = {
        "schema_version": 1,
        "claim": "Deterministic UTF-8 wire-size comparison with verified oracle reconstruction; no model, provider, quality, latency, cost, or success-rate measurement.",
        "accounting": "Compact ensure_ascii=False JSON. Modeled request bytes add output schema, raw retained prefix where used, and recorded metadata. Original task/system prompts and shared HTTP envelopes are excluded equally. Provider schema caching is not modeled. Unfinished prefix bytes are counted even though their value is regenerated.",
        "illustrative_response_budget_bytes": 1024,
        "budget_interpretation": "A uniform byte budget exposes response-size feasibility only. It is not a provider token limit and does not estimate probability of successful generation.",
        "limits": [
            "The fixtures are authored, not a representative workload or generated evaluation corpus.",
            "JSON Patch and known-slot baselines receive correctness credit after source validation and sealed-prefix checks.",
            "A handwritten typed suffix contract can reproduce the compiler's wire contract; the possible value is generic compilation, not an information-theoretic advantage over equivalent handwritten code.",
            "The source-inspired video fixture has unbounded arrays, so schema validity does not establish that all requested scenes were generated.",
            "Fewer response bytes can coexist with more total request-plus-response bytes, especially at early cuts, on tiny outputs, or inside a single long string.",
        ],
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(cases)} authored fixtures across five recovery methods; report: {args.output}")
    for case in cases:
        by_name = {row["method"]: row for row in case["methods"]}
        print(case["id"], {name: {"response": row["oracle_response_bytes"], "request_plus_response": row["modeled_request_plus_response_bytes"]} for name, row in by_name.items()})


if __name__ == "__main__":
    main()
