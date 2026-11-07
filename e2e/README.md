# Backend E2E

Five HTTP journeys cover login and tenant isolation, manual course creation and
publication, durable learning progress, original material delivery, and access
revocation with an old session. They complement the SQLite domain contracts in
`tests/`; no browser/frontend is involved.

Run from the repository root with development dependencies installed:

```powershell
docker run --detach --name lernium-e2e --publish 127.0.0.1:55439:5432 --env POSTGRES_USER=e2e --env POSTGRES_PASSWORD=local-e2e-only --env POSTGRES_DB=e2e_bootstrap pgvector/pgvector:pg17
$env:E2E_POSTGRES_DSN = 'postgresql+psycopg2://e2e:local-e2e-only@127.0.0.1:55439/e2e_bootstrap'
python -m pytest -c e2e/pytest.ini e2e -q
docker rm --force lernium-e2e
```

Wait for PostgreSQL readiness before running (`docker exec lernium-e2e pg_isready
-U e2e`). The supplied service must be disposable and its user must have
CREATEDB permission. Each run creates a randomly named database, migrates it with
Alembic, and drops only that database on teardown. Failures never trigger a
SQLite fallback. Each test has unique tenants and identities; the database and
temporary uploads are scoped to the run.

The harness starts the production `create_app("platform")` and `create_app("admin")`
on ephemeral loopback sockets. HTTP clients use password login; requests use
the application's normal database dependency and independent committed sessions.
Only initial organizations, users and delivery policy are seeded with SQLAlchemy.
Courses, lessons, questions, assignments, progress and materials use public APIs.
The repository `.env` is disabled for this process.

LLM and mail calls fail unless explicitly stubbed. Document embeddings return
deterministic IDs; upload validation, file storage, extraction, chunk persistence,
index worker (eager mode), release decisions and download checks remain real.
This suite does not prove Redis/RQ delivery or AI generation quality.

CI runs this suite as a separate job; image publication depends on it.
Ordinary `python -m pytest` continues to select `tests/` only. Run E2E separately
so the existing SQLite conftest cannot preconfigure the application.
