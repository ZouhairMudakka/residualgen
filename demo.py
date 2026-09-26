"""Reproducible compiler demonstration using authored data, not an LLM run."""

import json

from residualgen import compile_plan, parse_prefix


def closed(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def example():
    text = {"type": "string"}
    email = closed({"channel": {"type": "string", "enum": ["email"]}, "subject": text, "body": text})
    social = closed({"channel": {"type": "string", "enum": ["social"]}, "hook": text, "caption": text})
    schema = closed({
        "campaign": text,
        "deliverables": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"anyOf": [email, social]}},
        "review_status": {"type": "string", "enum": ["draft"]},
    })
    full = {
        "campaign": "Open-source developer launch",
        "deliverables": [
            {"channel": "email", "subject": "Keep the completed part of your JSON", "body": "An interrupted response need not discard every completed value. Try the local compiler demo and inspect its limits."},
            {"channel": "social", "hook": "What survives a dropped generation?", "caption": "The demo keeps completed values, requests the unfinished portion, and validates the reconstructed object. It does not claim better writing quality."},
            {"channel": "email", "subject": "A small recovery experiment", "body": "Compare the fresh continuation schema with the original schema, then inspect what was preserved."},
        ],
        "review_status": "draft",
    }
    raw = json.dumps(full, ensure_ascii=False, separators=(",", ":"))
    stop = raw.index('A small recovery') + len('A small')
    return schema, full, raw[:stop]


def main():
    schema, full, prefix = example()
    plan = compile_plan(schema, parse_prefix(prefix))
    # project is a fixture oracle. A real integration gets this payload from a
    # new model request constrained by plan.schema and given original context.
    reply = plan.project(full)
    assert plan.merge(reply) == full
    print("AUTHORED FIXTURE: no model request, no token/cost/quality measurement")
    print("\nINTERRUPTED RESPONSE\n" + prefix)
    print("\nFRESH CONTINUATION SCHEMA\n" + json.dumps(plan.schema, indent=2))
    print("\nFIXTURE CONTINUATION\n" + json.dumps(reply, indent=2))
    print("\nReconstruction equals the original authored fixture.")


if __name__ == "__main__":
    main()
