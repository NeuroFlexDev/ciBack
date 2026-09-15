from dataclasses import dataclass
from app.core.config import settings


@dataclass(frozen=True)
class ModelPolicy:
    model: str
    fallback: str
    max_output_tokens: int
    purpose: str


def model_policy(role: str) -> ModelPolicy:
    fast = settings.AI_MODEL_FAST
    reason = settings.AI_MODEL_REASONING
    writer = settings.AI_MODEL_WRITER
    critic = settings.AI_MODEL_CRITIC
    policies = {
        "ingestion": (fast, 6000, "Extract explicit knowledge from bounded document batches"),
        "competency_mapper": (reason, 10000, "Map evidence to measurable competencies"),
        "course_architect": (reason, 12000, "Plan objectives, prerequisites and exact lesson count"),
        "lesson_writer": (writer, 6000, "Write one lesson from retrieved evidence"),
        "assessment": (reason, 12000, "Produce answerable assessments and scoring rubrics"),
        "critic_qa": (critic, 6000, "Independently verify grounding and pedagogical quality"),
        "update": (reason, 6000, "Propose source-version-aware changes"),
        "chat": (writer, 2500, "Answer with conversation memory and course evidence"),
        "memory": (fast, 1500, "Compress conversation without inventing user preferences"),
        "vision": (fast, 4000, "Transcribe scanned source pages faithfully"),
        "canvas": (reason, 6000, "Propose scoped changes to a course graph"),
    }
    model, output, purpose = policies.get(role, policies["chat"])
    fallback = settings.AI_MODEL_FALLBACK
    if role in {"competency_mapper", "course_architect", "assessment", "update", "canvas"}:
        # Large connected JSON graphs need a structurally reliable fallback.
        # The cheap general fallback repeatedly truncated real textbook maps.
        fallback = critic if critic != model else fast
    if fallback == model:
        fallback = settings.AI_MODEL_REASONING
    if role == "critic_qa" and fallback == writer:
        fallback = fast
    return ModelPolicy(model, fallback, output, purpose)
