"""File-based compiler CLI. No model requests and no tool execution."""

import argparse
import json
from pathlib import Path
import sys

from . import compile_plan, parse_prefix


def _read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"Non-JSON constant: {value}")

    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def main():
    parser = argparse.ArgumentParser(description="Compile the unfinished portion of a structured JSON generation.")
    parser.add_argument("command", choices=["plan", "merge"])
    parser.add_argument("schema", help="Original JSON Schema file")
    parser.add_argument("prefix", help="Contiguous interrupted response text file")
    parser.add_argument("--reply", help="Fresh continuation JSON file (required for merge)")
    parser.add_argument("--out", help="Output JSON path; default stdout")
    args = parser.parse_args()
    if args.command == "merge" and not args.reply:
        parser.error("merge requires --reply")
    if args.command == "plan" and args.reply:
        parser.error("--reply is only valid with merge")
    try:
        plan = compile_plan(_read_json(args.schema), parse_prefix(Path(args.prefix).read_text(encoding="utf-8")))
        result = plan.schema if args.command == "plan" else plan.merge(_read_json(args.reply))
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
    except (ValueError, OSError, TypeError) as exc:
        print(f"residualgen: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
