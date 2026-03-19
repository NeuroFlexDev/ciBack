from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.routes import generation  # Генерация курса
from app.routes import (
    auth,
    chat,
    course_structure,
    courses,
    lessons,
    modules,
    tasks,
    tests,
    search,
    theories,
    upload,
    versioning,
    healthz,
)
from log import get_logger, set_request_id

logger = get_logger()


app = FastAPI(title="NeuroLearn Course API (with HuggingChat)", debug=settings.DEBUG)

origins = [s.strip() for s in settings.CORS_ORIGINS.split(",")]
# ✅ Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Разрешаем фронтенду доступ
    allow_credentials=True,
    allow_methods=["*"],  # Разрешаем все HTTP-методы
    allow_headers=["*"],  # Разрешаем все заголовки
)

@app.middleware("http")
async def logging_middleware(request: Request, call_next):

    rid = request.headers.get("X-Request-ID")
    set_request_id(rid)

    logger.info(f"Incoming {request.method} {request.url.path}")

    response = await call_next(request)

    logger.info(
        f"Completed {request.method} {request.url.path} "
        f"with status {response.status_code}"
    )

    response.headers["X-Request-ID"] = rid or ""
    return response

# ✅ Подключаем маршруты с префиксом /api
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

app.include_router(healthz.router, prefix="/api", tags=["Healthz"])
@app.get("/")
def root():
    return {"message": "Welcome to NeuroLearn Course API (DB + LLM Generation)"}
