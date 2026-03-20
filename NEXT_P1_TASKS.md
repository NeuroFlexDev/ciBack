# Next P1 Tasks

Current state:
- PR1 complete: config/auth hardening
- PR2 complete: DB runtime + Alembic alignment
- PR3 complete: API contract fixes
- PR4 complete: minimal RAG flow `upload -> index -> search`

This file is the next implementation backlog after the P0 stabilization wave.

## Recommended Order

1. LLM service hardening
2. Write-access protection and ownership model
3. Logging and health/readiness hardening
4. Route/service layer cleanup
5. README and OpenAPI sync

## Task 1. Harden LLM Service Layer

Why:
- generation paths still depend on provider-specific behavior
- timeout/retry/error handling is not normalized
- pilot reliability depends on predictable LLM failures

Files:
- `app/services/generation_service.py`
- `app/services/hf_infer_service.py`
- `app/services/gigachat_service.py`
- `app/core/config.py`

Minimum implementation:
- introduce one provider adapter contract
- add timeout settings from env
- add small retry policy for transient provider failures
- normalize exceptions into one service-level error shape
- log provider, model, latency, and token usage when available

Acceptance:
- generation code does not branch across providers in route handlers
- LLM timeouts produce controlled 4xx/5xx responses instead of raw tracebacks
- tests cover timeout and provider failure behavior

## Task 2. Protect Write Endpoints With Ownership Rules

Why:
- auth exists, but pilot still needs actual write protection
- without ownership rules, users can modify foreign content

Files:
- `app/routes/courses.py`
- `app/routes/modules.py`
- `app/routes/lessons.py`
- `app/routes/upload.py`
- `app/services/auth.py`
- `app/services/security.py`
- `app/models/user.py`
- new ownership fields or relation models if needed

Minimum implementation:
- require authenticated user for all create/update/delete endpoints
- add owner field for course or equivalent author relation
- allow write access only to owner or admin/editor role
- return `403` for cross-user modification attempts

Acceptance:
- anonymous write calls return `401`
- foreign-user write calls return `403`
- owner can edit only owned entities through course/module/lesson tree
- API tests cover happy path and access denial

## Task 3. Harden Logging and Health Checks

Why:
- pilot ops need useful runtime signals
- health endpoint should reflect DB and critical dependencies, not just process liveness

Files:
- `log.py`
- `main.py`
- `app/routes/healthz.py`
- `app/database/db.py`
- `app/services/embedding_service.py`

Minimum implementation:
- structured logs with stable keys
- keep request_id across request lifecycle
- add DB ping in `/api/healthz`
- report embedding/index readiness separately from general app health
- avoid logging prompts/responses verbatim when they may contain sensitive data

Acceptance:
- `/api/healthz` returns component-level status
- failing DB check is visible in endpoint response
- logs contain request_id, path, status_code, latency

## Task 4. Clean Route/Service Boundaries

Why:
- several routes still carry orchestration and ORM details directly
- pilot changes will be slower and riskier if route logic stays coupled

Files:
- `app/routes/*`
- `app/services/*`
- `app/repositories/*`
- dead or unused modules found during audit

Minimum implementation:
- move business decisions out of route handlers into service layer
- keep repository layer responsible for persistence access only
- remove or hide dead endpoints/modules that are not in pilot scope

Acceptance:
- route handlers mostly validate input, call service, return schema
- heavy business logic is no longer duplicated across routes
- dead or experimental endpoints are not exposed in public API

## Task 5. Sync Documentation With Reality

Why:
- pilot onboarding will fail if README/OpenAPI diverge from running code
- current codebase changed materially during PR1-PR4

Files:
- `README.md`
- route docstrings
- schema descriptions
- generated OpenAPI surface

Minimum implementation:
- document actual env selection and secrets
- document actual Postgres/Alembic startup flow
- document auth flow and bearer token usage
- document working RAG flow and `/api/search`

Acceptance:
- README startup instructions work on clean setup
- Swagger paths and examples match implemented endpoints
- no documented endpoint returns 404/410/500 under valid payloads
