"""Machine-readable health check. Exit 1 is suitable for existing external monitoring."""

import json

import app.models  # noqa: F401 -- register ORM mappings
from app.database.db import SessionLocal
from app.routes.operations import diagnostics


def main():
    with SessionLocal() as db:
        report = diagnostics(db)
    issues = []
    if report["redis"]["status"] != "up":
        issues.append("redis_unavailable")
    if report["disk"]["used_percent"] >= 90:
        issues.append("disk_above_90_percent")
    if report["outbox"].get("failed", 0):
        issues.append("outbound_delivery_failed")
    if report["outbox"]["oldest_pending_seconds"] > 600:
        issues.append("outbound_delivery_delayed")
    print(json.dumps(report | {"issues": issues}, default=str))
    raise SystemExit(1 if issues else 0)


if __name__ == "__main__":
    main()
