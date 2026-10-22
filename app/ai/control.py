"""Small live configuration cache. Never cache decrypted credentials.

Every process reloads within five seconds. A failed refresh fails closed instead
of restoring environment credentials that an operator may have revoked.
"""
import threading
import time
from copy import deepcopy
from app.core.config import AIRoutingCandidate, settings
from app.database.db import SessionLocal
from app.models.ai_control import AIControl

EMPTY = {"revision": 0, "providers": [], "candidates": [], "policy": {}}
_lock = threading.RLock()
_cached = None
_expires = 0.0


def invalidate():
    global _expires
    with _lock:
        _expires = 0.0


def read_snapshot():
    with SessionLocal() as db:
        row = db.get(AIControl, 1)
        return {"revision": row.revision, "providers": row.providers,
                "candidates": row.candidates, "policy": row.policy} if row else deepcopy(EMPTY)


def snapshot():
    global _cached, _expires
    with _lock:
        if time.monotonic() >= _expires:
            try:
                _cached = read_snapshot()
            except Exception:
                # No exception text: database diagnostics can contain secrets.
                _cached = None
            _expires = time.monotonic() + 5
        return deepcopy(_cached)


def policy():
    data = snapshot()
    base = {"mode": settings.AI_ROUTING_MODE, "local_only": settings.AI_LOCAL_ONLY,
            "min_samples": settings.AI_ROUTING_MIN_SAMPLES,
            "min_pass_rate": settings.AI_ROUTING_MIN_PASS_RATE}
    if data is not None:
        base.update(data["policy"])
    else:
        base["local_only"] = True
    return base


def candidates():
    data = snapshot()
    if data is None:
        return []
    rows = {(item.provider, item.model): item for item in settings.AI_ROUTING_CANDIDATES}
    rows.update({(item["provider"], item["model"]): AIRoutingCandidate.model_validate(item)
                 for item in data["candidates"]})
    return list(rows.values())
