# Contributing

## Branching

- Use focused branches such as `feature/<name>`, `fix/<name>`, `chore/<name>`, or `docs/<name>`.
- Do not mix unrelated migrations, refactors, and feature work in the same PR unless they are tightly coupled.

## Local Setup

- Install dependencies with `pip install -r requirements.txt`.
- Choose the runtime profile through `ENV=dev|stage|prod`.
- Treat `.env.dev`, `.env.stage`, and `.env.prod` as repository templates only; real deployment secrets must still come from the runtime environment.
- Before starting the API directly, run `alembic upgrade head`.

## Quality Gates

- Run `pytest -q` before opening a PR.
- If the schema changes, also run `alembic upgrade head` on a clean database.
- If a public endpoint changes, update schemas, tests, and docs in the same PR.

## Database Rules

- Do not reintroduce `Base.metadata.create_all()` into the runtime path.
- Model changes must ship with an Alembic migration.
- New migrations must work on SQLite and PostgreSQL-compatible paths used by the project.

## API Rules

- Public endpoints must use explicit request/response schemas.
- Avoid returning raw ORM objects or ad-hoc dictionaries for stable public contracts.
- Prefer service/repository boundaries over route-level business logic.

## Security Rules

- Keep JWT secrets in environment variables.
- Protect write endpoints with authentication and ownership/role checks.
- Do not log secrets, tokens, or full prompt/response bodies that may contain sensitive data.

## Testing Rules

- Every bugfix needs a regression test.
- Pilot-critical flows should have API or integration coverage.
- External LLM/providers must be mocked in tests unless the test is explicitly a staging smoke-check.
