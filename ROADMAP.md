# Roadmap

## Milestone 0: Pilot Ready

- Keep the core scope stable: auth, course CRUD, module/lesson CRUD, theory/tasks/tests CRUD, RAG upload/search, course graph, feedback, agent-assisted theory improvement, and version snapshot/restore.
- Run database changes only through Alembic and keep `alembic upgrade head` green on a clean database.
- Keep `docker-compose` startup migration-aware and make `/api/healthz` and `/api/readiness` reflect DB, vector index, temp storage, and queue status.
- Keep the public API covered by request/response schemas and regression tests.
- Keep `pytest -q` green before release.

## Milestone 1: Harden Pilot

- Add persistent/vector storage lifecycle beyond in-memory FAISS.
- Add refresh-token flow, token rotation, and rate limiting for LLM endpoints.
- Improve request logging into a more structured operational format.
- Expand OpenAPI examples and field descriptions for the pilot surface.
- Decide whether chat needs a dedicated course-QA endpoint separate from generic chat send.

## Milestone 2: Post-Pilot Expansion

- Background jobs for long-running generation and indexing.
- Storage abstraction for local disk vs. S3/MinIO.
- Metrics and tracing (Prometheus/OpenTelemetry).
- Compliance/PII processing.
- SCORM/xAPI export.
