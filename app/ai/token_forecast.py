"""Empirical base-token forecasts. Retries, repairs and cache hits are excluded."""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
import json
import math
from pathlib import Path
from statistics import median

from app.core.config import settings

VERSION = 1


def features(messages: list[dict], role: str, context: dict | None = None) -> dict:
    context = context or {}
    serialized = json.dumps(messages, ensure_ascii=False, default=str)
    units = 1.0
    if role == "ingestion":
        try:
            sources = json.loads(context.get("source_catalog_json", "[]"))
            units = max(1.0, sum(len(item.get("quote", "")) for item in sources) / 1000)
        except (ValueError, TypeError, AttributeError):
            pass
    elif role in {"competency_mapper", "course_architect"}:
        units = float(context.get("lesson_count", 1))
    elif role in {"assessment", "critic_qa"}:
        try:
            writer = json.loads(context.get("writer_artifact_json", "{}"))
            units = max(1, len(writer.get("lessons", [])))
        except (ValueError, TypeError, AttributeError):
            pass
    feedback = context.get("qa_feedback_json")
    return {"prompt_bytes": len(serialized.encode()), "units": max(1.0, units),
            "lesson_count": int(context.get("lesson_count", 1)),
            "has_qa_feedback": feedback not in (None, "", "null", "{}", "[]")}


def _solve(matrix, target):
    # Small ridge regression (three coefficients); no ML runtime or downloads.
    data = [list(row) + [value] for row, value in zip(matrix, target)]
    for i in range(len(target)):
        pivot = max(range(i, len(target)), key=lambda j: abs(data[j][i]))
        data[i], data[pivot] = data[pivot], data[i]
        divisor = data[i][i]
        if abs(divisor) < 1e-12:
            return None
        data[i] = [v / divisor for v in data[i]]
        for j in range(len(target)):
            if j != i:
                factor = data[j][i]
                data[j] = [a - factor * b for a, b in zip(data[j], data[i])]
    return [row[-1] for row in data]


def fit(rows: list[dict]) -> dict:
    clean = [row for row in rows if row.get("status") == "completed"
             and row.get("usage_reported") and row.get("attempt") == 1
             and not row.get("features", {}).get("has_qa_feedback")
             and not row.get("features", {}).get("is_revision")
             and row.get("features", {}).get("prompt_bytes", 0) > 0
             and row.get("input_tokens", 0) > 0 and row.get("output_tokens", 0) > 0]
    groups = defaultdict(list)
    for row in clean:
        groups[f'{row["role"]}|{row["model"]}'].append(row)
    models = {}
    for key, observations in groups.items():
        matrix = [[0.0] * 3 for _ in range(3)]
        target = [0.0] * 3
        for row in observations:
            f = row["features"]
            x = [1.0, f["prompt_bytes"] / 1000, f["units"]]
            for i in range(3):
                target[i] += x[i] * row["output_tokens"]
                for j in range(3):
                    matrix[i][j] += x[i] * x[j]
        for i in range(3):
            matrix[i][i] += 0.1 if i == 0 else 10.0
        coefficients = _solve(matrix, target) if len(observations) >= 5 else None
        models[key] = {
            "samples": len(observations),
            "courses": len({row["course_key"] for row in observations}),
            "input_per_byte": median(row["input_tokens"] / row["features"]["prompt_bytes"] for row in observations),
            "output_per_unit": median(row["output_tokens"] / row["features"]["units"] for row in observations),
            "prompt_bytes_per_unit": median(row["features"]["prompt_bytes"] / row["features"]["units"] for row in observations),
            "output_coefficients": coefficients,
            "prompt_bytes_range": [min(row["features"]["prompt_bytes"] for row in observations),
                                   max(row["features"]["prompt_bytes"] for row in observations)],
        }
    return {"version": VERSION, "scope": "base_tokens_without_retries_or_repairs", "models": models,
            "samples": len(clean), "excluded_samples": len(rows) - len(clean),
            "courses": len({row["course_key"] for row in clean})}


def predict(model: dict, *, role: str, model_id: str, request_features: dict,
            max_output_tokens: int) -> dict:
    data = model.get("models", {}).get(f"{role}|{model_id}")
    prompt_bytes = request_features["prompt_bytes"]
    units = request_features.get("units", 1.0)
    if data:
        inputs = prompt_bytes * data["input_per_byte"]
        coefficients = data.get("output_coefficients")
        outputs = (sum(a * b for a, b in zip(coefficients, [1, prompt_bytes / 1000, units]))
                   if coefficients else units * data["output_per_unit"])
        outside_range = not data["prompt_bytes_range"][0] <= prompt_bytes <= data["prompt_bytes_range"][1]
        uncertainty = 0.65 if data["courses"] < 10 or outside_range else 0.35
        method = "empirical_ridge" if coefficients else "empirical_median"
    else:
        inputs = prompt_bytes / 3
        outputs = max_output_tokens * 0.45
        uncertainty = 0.85
        method = "uncalibrated_prior"
    point_in = max(1, min(prompt_bytes, round(inputs)))
    point_out = max(1, min(max_output_tokens, round(outputs)))
    return {"input_tokens": point_in, "output_tokens": point_out,
            "input_range": [max(1, round(point_in * (1 - uncertainty))), min(prompt_bytes, math.ceil(point_in * (1 + uncertainty)))],
            "output_range": [max(1, round(point_out * (1 - uncertainty))), min(max_output_tokens, math.ceil(point_out * (1 + uncertainty)))],
            "method": method, "samples": data["samples"] if data else 0,
            "courses": data["courses"] if data else 0,
            "provisional": not data or data["courses"] < 10,
            "range_kind": "heuristic_not_calibrated_confidence_interval",
            "includes_retries": False}


@lru_cache(maxsize=4)
def _load(path: str, modified: int) -> dict:
    try:
        model = json.loads(Path(path).read_text())
        return model if model.get("version") == VERSION else {}
    except (OSError, ValueError):
        return {}


def current_model() -> dict:
    path = settings.AI_TOKEN_FORECAST_PATH
    try:
        return _load(str(path), path.stat().st_mtime_ns)
    except OSError:
        return {}


def cross_validate(rows: list[dict]) -> dict:
    """Hold out an entire course; never split calls of one course across folds."""
    predictions = []
    courses = sorted({row["course_key"] for row in rows})
    for course in courses:
        trained = fit([row for row in rows if row["course_key"] != course])
        for row in rows:
            if (row["course_key"] != course or row.get("status") != "completed"
                    or not row.get("usage_reported") or row.get("attempt") != 1
                    or row["features"].get("has_qa_feedback")):
                continue
            if row["features"].get("is_revision"):
                continue
            prediction = predict(trained, role=row["role"], model_id=row["model"],
                                 request_features=row["features"], max_output_tokens=row["max_output_tokens"])
            if prediction["method"] == "uncalibrated_prior":
                continue
            predictions.append({"course_key": course, "role": row["role"],
                                "actual_input": row["input_tokens"], "predicted_input": prediction["input_tokens"],
                                "actual_output": row["output_tokens"], "predicted_output": prediction["output_tokens"]})
    metrics = {}
    for side in ("input", "output"):
        denominator = sum(row[f"actual_{side}"] for row in predictions)
        metrics[f"{side}_wape"] = (sum(abs(row[f"actual_{side}"] - row[f"predicted_{side}"]) for row in predictions)
                                   / denominator if denominator else None)
    return {"method": "leave_one_course_out", "held_out_courses": len(courses),
            "evaluated_calls": len(predictions), "metrics": metrics, "predictions": predictions}


def estimate_course(*, source_characters: int, lessons: int) -> dict:
    """Preflight approximation of the existing batching plan, with no AI calls."""
    from decimal import Decimal
    from app.ai.models import model_policy
    from app.ai import routing, gateway
    from app.ai.providers import route_id
    from app.ai.pricing import candidate_price, SNAPSHOT
    model = current_model()
    available = gateway.available_provider_ids()
    batch_chars = settings.AI_INGESTION_BATCH_CHARS
    source_units = [max(1, min(batch_chars, source_characters - offset) / 1000)
                    for offset in range(0, max(1, source_characters), batch_chars)]
    pairs = [min(2, lessons - offset) for offset in range(0, lessons, 2)]
    plan = {"ingestion": source_units, "competency_mapper": [lessons], "course_architect": [lessons],
            "lesson_writer": [1] * lessons, "assessment": pairs, "critic_qa": pairs}
    stages = []
    for role, units_list in plan.items():
        policy = model_policy(role)
        stage = {"role": role, "calls": len(units_list), "input_tokens": 0, "output_tokens": 0,
                 "input_range": [0, 0], "output_range": [0, 0], "cost_rub": "0", "calibrated": True}
        for units in units_list:
            profile = model.get("models", {}).get(f"{role}|{policy.model}")
            # Future prompts depend on artifacts not generated yet. Their size
            # is estimated from observed requests, separately from tokenization.
            prompt_bytes = round(profile["prompt_bytes_per_unit"] * units) if profile else round(8000 + units * 4000)
            f = {"prompt_bytes": max(1, prompt_bytes), "units": units}
            predictions = {route_id(item.provider, item.model): predict(model, role=role, model_id=item.model,
                request_features=f, max_output_tokens=policy.max_output_tokens) for item in routing.enabled_candidates(role)}
            predictions[route_id("vsellm", policy.model)] = predict(model, role=role, model_id=policy.model,
                request_features=f, max_output_tokens=policy.max_output_tokens)
            decision = routing.choose_route(role=role, baseline_model=policy.model, input_tokens=prompt_bytes,
                output_tokens=policy.max_output_tokens, available_providers=available, forecast=predictions)
            prediction = predict(model, role=role, model_id=decision.selected_model, request_features=f,
                                 max_output_tokens=policy.max_output_tokens)
            stage["model"] = decision.selected_model
            stage["provider"] = decision.selected_provider
            stage["calibrated"] = stage["calibrated"] and prediction["samples"] > 0
            for side in ("input", "output"):
                stage[f"{side}_tokens"] += prediction[f"{side}_tokens"]
                for index in (0, 1):
                    stage[f"{side}_range"][index] += prediction[f"{side}_range"][index]
            price = candidate_price(decision.selected_provider, decision.selected_model, role)
            if price and prediction["input_tokens"] > price.max_input_tokens:
                price = None
            if price and stage["cost_rub"] is not None:
                stage["cost_rub"] = str(Decimal(stage["cost_rub"]) + routing.estimate_cost(
                    price, prediction["input_tokens"], prediction["output_tokens"]))
            else:
                stage["cost_rub"] = None
        stages.append(stage)
    return {"stages": stages, "input_tokens": sum(row["input_tokens"] for row in stages),
            "output_tokens": sum(row["output_tokens"] for row in stages),
            "cost_rub": str(sum(Decimal(row["cost_rub"]) for row in stages))
                if all(row["cost_rub"] is not None for row in stages) else None,
            "source_characters": source_characters, "lesson_count": lessons,
            "training_courses": model.get("courses", 0), "training_calls": model.get("samples", 0),
            "provisional": True, "includes_retries": False, "includes_cache_discount": False,
            "default_price_snapshot": SNAPSHOT,
            "excluded": ["repairs", "retries", "embeddings", "OCR", "external_tools"],
            "range_kind": "heuristic_not_calibrated_confidence_interval"}
