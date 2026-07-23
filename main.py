import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import agent, auth, chat, courses, feedback, graph, lessons, modules, search, tasks, tests, upload
from app.routes import course_generator
from app.routes import course_structure
from app.routes import healthz
from app.routes import theories
from app.routes import versioning
from app.services.auth_service import get_current_user

app = FastAPI(title="Lernium API")

cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,https://lernium.ru,https://www.lernium.ru",
).split(",")

# ✅ Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in cors_origins if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],  # Разрешаем все HTTP-методы
    allow_headers=["*"],  # Разрешаем все заголовки
)

# ✅ Подключаем маршруты с префиксом /api
app.include_router(auth.router, prefix="/api", tags=["Auth"])
protected_dependencies = [Depends(get_current_user)]

app.include_router(courses.router, prefix="/api", tags=["Courses"], dependencies=protected_dependencies)
app.include_router(modules.router, prefix="/api", tags=["Modules"], dependencies=protected_dependencies)
app.include_router(lessons.router, prefix="/api", tags=["Lessons"], dependencies=protected_dependencies)
app.include_router(theories.router, prefix="/api", tags=["Theory"], dependencies=protected_dependencies)
app.include_router(tasks.router, prefix="/api", tags=["Tasks"], dependencies=protected_dependencies)
app.include_router(tests.router, prefix="/api", tags=["Tests"], dependencies=protected_dependencies)
app.include_router(upload.router, prefix="/api", tags=["Files"], dependencies=protected_dependencies)
app.include_router(course_structure.router, prefix="/api", tags=["Course Structure"], dependencies=protected_dependencies)
app.include_router(course_generator.router, prefix="/api", tags=["Course Generation"], dependencies=protected_dependencies)
app.include_router(versioning.router, prefix="/api", tags=["Course Versions"], dependencies=protected_dependencies)
app.include_router(chat.router, prefix="/api", dependencies=protected_dependencies)
app.include_router(graph.router, prefix="/api", tags=["Graph"], dependencies=protected_dependencies)
app.include_router(search.router, prefix="/api", tags=["Search"], dependencies=protected_dependencies)
app.include_router(agent.router, prefix="/api", tags=["Agent"], dependencies=protected_dependencies)
app.include_router(feedback.router, prefix="/api", tags=["Feedback"], dependencies=protected_dependencies)
app.include_router(healthz.router, prefix="/api", tags=["Healthz"])

@app.get("/")
def root():
    return {"message": "Lernium API"}
