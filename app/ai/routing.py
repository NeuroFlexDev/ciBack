"""Route each request among approved providers/models without extra inference."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from app.core.config import AIRoutingCandidate, settings
from app.ai.providers import route_id
from app.ai.pricing import candidate_price, SNAPSHOT


ROUTER_VERSION = 2


def policy_fingerprint() -> str:
    payload = {
        "version": ROUTER_VERSION,
        "price_snapshot": SNAPSHOT,
        "endpoint": settings.VSELLM_BASE_URL,
        "providers": [item.model_dump(mode="json") for item in settings.AI_PROVIDERS],
        "candidates": [item.model_dump(mode="json") for item in settings.AI_ROUTING_CANDIDATES],
        "min_samples": settings.AI_ROUTING_MIN_SAMPLES,
        "min_pass_rate": settings.AI_ROUTING_MIN_PASS_RATE,
        "history_limit": settings.AI_ROUTING_HISTORY_LIMIT,
        "cooldown": settings.AI_ROUTING_COOLDOWN_SECONDS,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def cache_policy() -> dict:
    if settings.AI_ROUTING_MODE == "off":
        return {}
    return {"_routing": policy_fingerprint(), "_routing_mode": settings.AI_ROUTING_MODE}


def enabled_candidates(role: str) -> list[AIRoutingCandidate]:
    if settings.AI_ROUTING_MODE == "off":
        return []
    return [item for item in settings.AI_ROUTING_CANDIDATES
            if item.approved and role in item.roles and item.json_mode
            and (role != "critic_qa" or item.independent_review and item.model != settings.AI_MODEL_WRITER)
            and (role != "vision" or item.vision)]


def token_bucket(input_tokens: int) -> str:
    return "short" if input_tokens <= 8192 else "medium" if input_tokens <= 32768 else "long"


def estimate_cost(candidate: AIRoutingCandidate, input_tokens: int, output_tokens: int) -> Decimal:
    """Uncached text-token estimate in RUB, including reserved output, not an invoice."""
    return (Decimal(input_tokens) * candidate.input_rub_per_million
            + Decimal(output_tokens) * candidate.output_rub_per_million) / Decimal(1_000_000)


@dataclass(frozen=True)
class RouteDecision:
    selected_model: str
    proposed_model: str
    metadata: dict
    selected_provider: str = "vsellm"


def choose_route(
    *, role: str, baseline_model: str, input_tokens: int, output_tokens: int,
    history: Iterable[dict] = (), excluded_models: set[str] | None = None,
    min_tier: int = 0, now: float | None = None,
    available_providers: set[str] | None = None,
    forecast: dict | None = None,
    requested_model: str | None = None,
    input_estimates: dict[str, int] | None = None,
) -> RouteDecision:
    """Rank explicitly approved candidates by estimated cost per contract pass.

    History is already scoped to the owner and role by the caller. Only the same
    policy and input-size bucket participate; API success alone is not a pass.
    The existing static policy is the recovery path when no candidate qualifies.
    """
    now = time.time() if now is None else now
    signature = policy_fingerprint()
    bucket = token_bucket(input_tokens)
    observations = [row for row in history
                    if row.get("policy") == signature and row.get("input_bucket") == bucket
                    and row.get("outcome") in {"contract_pass", "contract_fail", "provider_error"}]
    ranked = []
    candidates = enabled_candidates(role)
    baseline = candidate_price("vsellm", baseline_model, role)
    if settings.AI_ROUTING_MODE != "off" and baseline and not any(
            row.provider == "vsellm" and row.model == baseline_model for row in candidates):
        candidates.append(baseline)
    for candidate in candidates:
        if requested_model is not None and candidate.model != requested_model:
            continue
        if (candidate.model in (excluded_models or set())
                or route_id(candidate.provider, candidate.model) in (excluded_models or set())
                or candidate.tier < min_tier):
            continue
        if available_providers is not None and candidate.provider not in available_providers:
            continue
        candidate_input = (input_estimates or {}).get(route_id(candidate.provider, candidate.model), input_tokens)
        if (candidate_input > min(candidate.max_input_tokens, settings.AI_MAX_INPUT_TOKENS)
                or output_tokens > candidate.max_output_tokens
                or candidate_input + output_tokens > candidate.context_window):
            continue
        samples = sorted((row for row in observations if row.get("selected_model") == candidate.model
                          and row.get("selected_provider", "vsellm") == candidate.provider),
                         key=lambda row: row.get("finished_at", 0), reverse=True)
        # Three recent transport failures open a temporary circuit. Validation
        # failures are handled by the pass-rate floor and per-step escalation.
        if (len(samples) >= 3 and all(row["outcome"] == "provider_error" for row in samples[:3])
                and now - samples[0].get("finished_at", 0) < settings.AI_ROUTING_COOLDOWN_SECONDS):
            continue
        contracts = [row for row in samples if row["outcome"] != "provider_error"]
        passes = sum(row["outcome"] == "contract_pass" for row in contracts)
        if (len(contracts) >= settings.AI_ROUTING_MIN_SAMPLES
                and passes / len(contracts) < settings.AI_ROUTING_MIN_PASS_RATE):
            continue
        # A fixed 9/10 prior avoids certainty from one success. This is a ranking
        # heuristic; it is not a calibrated probability of final course quality.
        probability = Decimal(9 + passes) / Decimal(10 + len(samples))
        predicted = (forecast or {}).get(route_id(candidate.provider, candidate.model), {})
        cost = estimate_cost(candidate, predicted.get("input_tokens", candidate_input),
                             predicted.get("output_tokens", output_tokens))
        ranked.append((cost / probability, route_id(candidate.provider, candidate.model), candidate,
                       cost, probability, len(samples)))
    ranked.sort(key=lambda row: (row[0], row[1]))
    winner = ranked[0] if ranked else None
    proposed = winner[2].model if winner else baseline_model
    proposed_provider = winner[2].provider if winner else "vsellm"
    selected = proposed if settings.AI_ROUTING_MODE == "adaptive" else baseline_model
    selected_provider = proposed_provider if settings.AI_ROUTING_MODE == "adaptive" else "vsellm"
    selected_candidate = candidate_price(selected_provider, selected, role)
    metadata = {
        "version": ROUTER_VERSION, "mode": settings.AI_ROUTING_MODE, "policy": signature,
        "selected_model": selected, "proposed_model": proposed,
        "selected_provider": selected_provider, "proposed_provider": proposed_provider,
        "baseline_model": baseline_model, "input_bucket": bucket,
        "reason": "lowest_estimated_cost_per_contract_pass" if winner else "static_recovery",
        "tier": selected_candidate.tier if selected_candidate else None,
        "samples": winner[5] if winner else 0,
        "rank_score_rub": str(winner[0]) if winner else None,
        "estimated_uncached_rub": str(estimate_cost(selected_candidate, input_tokens, output_tokens))
            if selected_candidate and input_tokens <= selected_candidate.max_input_tokens else None,
        "prices": {"input_rub_per_million": str(selected_candidate.input_rub_per_million),
                   "output_rub_per_million": str(selected_candidate.output_rub_per_million),
                   "max_input_tokens": selected_candidate.max_input_tokens}
            if selected_candidate else None,
    }
    return RouteDecision(selected, proposed, metadata, selected_provider)


def record_outcome(metadata: dict | None, *, outcome: str, usage: dict | None = None) -> dict | None:
    if metadata is None:
        return None
    result = {**metadata, "outcome": outcome, "finished_at": time.time()}
    prices = result.get("prices")
    if usage and prices and usage.get("input_tokens", 0) <= prices["max_input_tokens"]:
        # Cache discounts and non-token charges are deliberately not invented.
        result["usage_priced_uncached_rub"] = str((
            Decimal(usage["input_tokens"]) * Decimal(prices["input_rub_per_million"])
            + Decimal(usage["output_tokens"]) * Decimal(prices["output_rub_per_million"])
        ) / Decimal(1_000_000))
    return result
