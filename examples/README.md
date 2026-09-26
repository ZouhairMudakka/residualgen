# Authored round-trip example

These files come from `demo.py`. They are authored fixture data and oracle projections, not model responses. `interrupted.txt` intentionally ends inside a string.

From the repository root:

```bash
python -m residualgen plan examples/campaign.schema.json examples/interrupted.txt
python -m residualgen merge examples/campaign.schema.json examples/interrupted.txt --reply examples/continuation.json
```

The merged result equals `complete.json`. In an actual integration, replace the fixture continuation with a fresh generated payload that conforms to the plan schema and retains the original task context.
