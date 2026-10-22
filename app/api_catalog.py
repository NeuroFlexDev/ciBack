import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import agent, auth, chat, course_content, courses, feedback, graph, lessons, modules, pipeline, search, tasks, tests, upload
from app.routes import course_generation_settings, course_generator
from app.routes import course_structure
from app.routes import healthz
from app.routes import theories
from app.routes import versioning
from app.services.auth_service import get_current_user

app = FastAPI(title="Lernium API")
from app.core.http_security import SecurityMiddleware
from app.routes import account
from app.routes import legal
app.add_middleware(SecurityMiddleware)
from app.core.upload_limits import UploadBodyLimitMiddleware
app.add_middleware(UploadBodyLimitMiddleware)
from app.routes import administration
app.include_router(administration.router, prefix="/api", tags=["Administration"])
from app.routes import business
app.include_router(business.router, prefix="/api", tags=["Business"])
from app.routes import learning
app.include_router(learning.router, prefix="/api", tags=["Learning"])
from app.routes import support_access
app.include_router(support_access.router, prefix="/api", tags=["Support Access"])
from app.routes import course_transfer
app.include_router(course_transfer.router, prefix="/api", tags=["Course Transfer"])
app.include_router(account.router, prefix="/api", tags=["Account"])
from app.routes import mfa
app.include_router(mfa.router, prefix="/api", tags=["Two-factor authentication"])
app.include_router(legal.router, prefix="/api", tags=["Legal"])
from app.routes import integrations, billing, oidc, lti, operations
app.include_router(integrations.router, prefix="/api", tags=["Integrations v1"])
app.include_router(billing.router, prefix="/api", tags=["Billing"])
app.include_router(oidc.router, prefix="/api", tags=["OIDC"])
app.include_router(lti.router, prefix="/api", tags=["LTI 1.3"])
app.include_router(operations.router, prefix="/api", tags=["Operations"])
from app.routes import ai_workspace
from app.routes import materials
app.include_router(materials.router, prefix="/api", tags=["Materials"])
from app.routes import learner_assistant
app.include_router(learner_assistant.router, prefix="/api", tags=["Learner Assistant"])
from app.routes import ai_grading
app.include_router(ai_grading.router, prefix="/api", tags=["AI Review"])
app.include_router(ai_workspace.router, prefix="/api", tags=["AI Workspace"])

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
app.include_router(course_content.router, prefix="/api", tags=["Course Content"], dependencies=protected_dependencies)
app.include_router(course_generation_settings.router, prefix="/api", tags=["Course Generation Settings"], dependencies=protected_dependencies)
app.include_router(pipeline.router, prefix="/api", tags=["Document Pipeline"], dependencies=protected_dependencies)
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
