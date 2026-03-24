# NeuroLearn Backend (`ciBack`)

FastAPI backend for course authoring, LLM-assisted generation, and course-scoped RAG.

Current pilot scope includes:

- auth and owner/role-based write access
- CRUD for courses, modules, lessons, theory, tasks, and tests
- document upload, indexing, semantic search, and course description enrichment
- chat sessions with persisted history and optional course context
- feedback, course graph, and agent-assisted theory improvement
- course snapshot/restore versioning
- health and readiness endpoints

## Runtime Profiles

Configuration is centralized in [app/core/config.py](./app/core/config.py) and loaded through `pydantic-settings`.

- `ENV=dev` -> `.env.dev`
- `ENV=stage` -> `.env.stage`
- `ENV=prod` -> `.env.prod`

The repository keeps `.env.dev`, `.env.stage`, and `.env.prod` as templates that show the expected shape of configuration. Real deployment secrets must still come from the runtime environment.

Main settings are documented in [.env.example](./.env.example):

```env
ENV=dev
APP_HOST=0.0.0.0
APP_PORT=8000
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
DATABASE_URL=sqlite:///./database.db
JWT_SECRET=change-me
JWT_ALG=HS256
ACCESS_TOKEN_TTL_MINUTES=15
REFRESH_TOKEN_TTL_MINUTES=1440
LOG_LEVEL=INFO
```

## Local Run

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn main:app --reload
```

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
uvicorn main:app --reload
```

After startup:

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Health: `http://localhost:8000/api/healthz`
- Readiness: `http://localhost:8000/api/readiness`

## Docker Compose

`docker-compose.yml` starts PostgreSQL and the API container. The API entrypoint runs Alembic before the app starts.

```bash
docker compose up --build
```

The compose setup uses `.env.dev` plus an explicit PostgreSQL `DATABASE_URL` override for the `web` service.

## Health and Readiness

`/api/healthz` and `/api/readiness` currently report:

- database status
- vector index status
- temp file storage status
- background queue status

Example response:

```json
{
  "ok": true,
  "db": "up",
  "vector_index": {
    "status": "up",
    "documents": 3
  },
  "file_storage": {
    "status": "up",
    "path": "/tmp"
  },
  "queue": {
    "status": "not_configured"
  }
}
```

## API Surface

Main route groups:

- `Auth`
- `Courses`, `Modules`, `Lessons`, `Theory`, `Tasks`, `Tests`
- `Files`, `Search`, `Course Structure`, `Course Generation`
- `Chat`
- `Feedback`
- `Graph`
- `Agent`
- `Course Versions`
- `Healthz`

Pilot-critical endpoints include:

```http
POST /api/auth/register
POST /api/auth/login
GET  /api/courses/{course_id}/generate_modules
POST /api/courses/{course_id}/upload-description
GET  /api/search
POST /api/chat/
POST /api/chat/send
POST /api/feedback/
GET  /api/graph?course_id={course_id}
POST /api/agent/improve-theory
POST /api/versions/course/{course_id}/snapshot
POST /api/versions/course-version/{course_version_id}/restore
GET  /api/healthz
GET  /api/readiness
```

## Architecture

The backend is organized into explicit layers:

- `app/routes`: HTTP contracts and endpoint wiring
- `app/schemas`: request/response Pydantic models
- `app/services`: orchestration, LLM, RAG, access-control, versioning
- `app/repositories`: data-access layer
- `app/models`: SQLAlchemy models
- `app/core`: configuration and shared runtime concerns
- `app/database`: engine, session, and metadata wiring

## Database and Migrations

- Schema changes must go through Alembic migrations.
- Run `alembic upgrade head` before local startup if the database is not current.
- The test suite includes migration coverage on a clean database.

Recent pilot changes include:

- persisted chat sessions/messages
- lesson-scoped task/test generation
- course snapshot data for restore

## Testing

Run the full suite:

```bash
pytest -q
```

Useful targeted checks:

```bash
pytest tests/test_smoke.py -q
pytest tests/test_chat_api.py -q
pytest tests/test_versioning_api.py -q
pytest tests/test_upload_content_api.py -q
pytest tests/test_alembic_migrations.py -q
```

Current baseline: `54 passed`.

## Known Post-Pilot Work

These areas are intentionally not finished for the current pilot:

- persistent FAISS lifecycle beyond in-memory runtime state
- refresh-token rotation and revocation
- rate limiting for LLM-heavy endpoints
- background jobs for long-running generation and indexing
- storage abstraction for local disk vs. S3/MinIO
- metrics, tracing, compliance/PII, and SCORM/xAPI export
- richer OpenAPI examples and field-level docs

## Related Docs

- [ROADMAP.md](./ROADMAP.md)
- [CONTRIBUTING.md](./CONTRIBUTING.md)
