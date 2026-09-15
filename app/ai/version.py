"""Invalidate run/checkpoint reuse after model, prompt, schema or graph changes."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from app.core.config import settings


@lru_cache(maxsize=1)
def implementation_digest():
    root = Path(__file__).resolve().parents[1]
    files = [*root.joinpath("prompts").glob("*.j2"), *root.joinpath("ai").glob("*.py"),
             root / "schemas/agentic_pipeline.py", root / "services/agentic_course_pipeline.py",
             root / "services/agent_runtime.py", root / "services/generation_service.py"]
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_signature():
    config = {key: str(value) for key, value in settings.model_dump().items()
              if key.startswith("AI_MODEL_") or key in {"AI_QA_MIN_SCORE", "AI_MAX_REVISIONS", "AI_INGESTION_BATCH_CHARS"}}
    return hashlib.sha256((implementation_digest() + json.dumps(config, sort_keys=True)).encode()).hexdigest()
