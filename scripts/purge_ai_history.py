"""Dry-run by default. Explicit execution requires a policy and matching digest.

First inspect: python -m scripts.purge_ai_history --history-days 90
--policy-reference <approved policy identifier>. Repeat the same options with
--execute --plan-digest <digest from dry-run> to execute that exact plan.
"""
import argparse
import json
from app.database.db import SessionLocal
from app.services.ai_retention import RetentionPolicy, retention_plan, apply_retention


def purge(db, *, execute=False, expected_digest=None, **policy_values):
    policy = RetentionPolicy(**policy_values)
    return apply_retention(db, policy, expected_digest=expected_digest) if execute else retention_plan(db, policy)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-days", type=int)
    parser.add_argument("--artifact-days", type=int)
    parser.add_argument("--draft-version-days", type=int)
    parser.add_argument("--policy-reference", default="")
    parser.add_argument("--plan-digest")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        result = purge(session, execute=args.execute, expected_digest=args.plan_digest, history_days=args.history_days,
            artifact_days=args.artifact_days, draft_version_days=args.draft_version_days, policy_reference=args.policy_reference)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
