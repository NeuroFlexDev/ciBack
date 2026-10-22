"""Explicit RUB/1M price snapshot for existing routes, verified 2026-10-19.

Sources: https://vsellm.ru/models/openai/gpt-5.4-mini
https://vsellm.ru/models/anthropic/claude-sonnet-4.6
https://vsellm.ru/models/google/gemini-2.5-flash
Operator candidate prices override this snapshot. No FX conversion or tools.
"""
from app.core.config import AIRoutingCandidate, settings

SNAPSHOT = "2026-10-19"
RATES = {"openai/gpt-5.4-mini": (83, 497), "anthropic/claude-sonnet-4.6": (332, 1658),
         "google/gemini-2.5-flash": (33, 276)}


def candidate_price(provider: str, model: str, role: str = "chat"):
    configured = next((row for row in settings.AI_ROUTING_CANDIDATES
                       if row.provider == provider and row.model == model), None)
    if configured:
        return configured
    if provider != "vsellm" or model not in RATES:
        return None
    from app.ai.context_budget import MODEL_LIMITS
    context_window, max_output = MODEL_LIMITS[model]
    input_price, output_price = RATES[model]
    return AIRoutingCandidate(provider=provider, model=model, roles=[role], approved=True,
        input_rub_per_million=input_price, output_rub_per_million=output_price,
        max_input_tokens=settings.AI_MAX_INPUT_TOKENS, max_output_tokens=max_output,
        context_window=context_window, independent_review=role == "critic_qa", vision=role == "vision")
