from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routes import generation
from app.routes import (
    agent,
    auth,
    chat,
    course_structure,
    courses,
    feedback,
    graph,
    healthz,
    lessons,
    modules,
    search,
    tasks,
    tests,
    theories,
    upload,
    versioning,
)
from log import clear_request_id, get_logger, set_request_id

logger = get_logger()

app = FastAPI(
    title="NeuroLearn Course API",
    description=(
        "Pilot backend for course authoring, RAG-assisted generation, "
        "chat, feedback, graph, and version snapshot/restore."
    ),
    debug=settings.DEBUG,
)

origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    request_id = set_request_id(request.headers.get("X-Request-ID"))
    started_at = perf_counter()
    logger.info(f"Incoming {request.method} {request.url.path}")

    try:
        response = await call_next(request)
        elapsed_ms = int((perf_counter() - started_at) * 1000)
        logger.info(
            f"Completed {request.method} {request.url.path} "
            f"with status {response.status_code} in {elapsed_ms}ms"
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        clear_request_id()


app.include_router(courses.router, prefix="/api", tags=["Courses"])
app.include_router(modules.router, prefix="/api", tags=["Modules"])
app.include_router(lessons.router, prefix="/api", tags=["Lessons"])
app.include_router(theories.router, prefix="/api", tags=["Theory"])
app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
app.include_router(tests.router, prefix="/api", tags=["Tests"])
app.include_router(upload.router, prefix="/api", tags=["Files"])
app.include_router(search.router, prefix="/api", tags=["Search"])
app.include_router(course_structure.router, prefix="/api", tags=["Course Structure"])
app.include_router(generation.router, prefix="/api", tags=["Course Generation"])
app.include_router(versioning.router, prefix="/api", tags=["Course Versions"])
app.include_router(auth.router, prefix="/api", tags=["Auth"])
app.include_router(chat.router, prefix="/api")
app.include_router(feedback.router, prefix="/api")
app.include_router(graph.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(healthz.router, prefix="/api", tags=["Healthz"])


@app.get("/")
def root():
    return {"message": "Welcome to NeuroLearn Course API (DB + LLM Generation)"}
