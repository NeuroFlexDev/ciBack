"""Fit a base-token model from isolated course reports, without network calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def observations(paths):
    rows, seen = [], set()
    for path in paths:
        report = json.loads(path.read_text())
        identity = report.get("scope", {"evaluation": str(path.parent)})
        course_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        for index, call in enumerate(report.get("calls", [])):
            route = call.get("routing") or {}
            observation = route.get("observation")
            if not observation:
                continue
            key = (course_key, call.get("id", index))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"course_key": course_key, "call_id": key[1], "role": call["agent"],
                         "model": route.get("selected_model", call["model"]),
                         "provider": route.get("selected_provider", "vsellm"),
                         "status": call["status"], "input_tokens": call["input_tokens"],
                         "output_tokens": call["output_tokens"], **observation})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observations-output", type=Path, required=True)
    parser.add_argument("--validation-output", type=Path, required=True)
    args = parser.parse_args()
    from app.ai.token_forecast import fit, cross_validate
    rows = observations(args.reports)
    model = fit(rows)
    if not model["samples"]:
        parser.error("No measured, successful first-pass observations; reservations are not training data")
    validation = cross_validate(rows)
    for path in (args.output, args.observations_output, args.validation_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(model, ensure_ascii=False, indent=2))
    args.observations_output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    args.validation_output.write_text(json.dumps(validation, ensure_ascii=False, indent=2))
    print(json.dumps({"samples": model["samples"], "courses": model["courses"],
                      "excluded_samples": model["excluded_samples"], "validation": validation["metrics"],
                      "evaluated_calls": validation["evaluated_calls"]}))


if __name__ == "__main__":
    main()
