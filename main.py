from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database.db import Base, engine
from app.routes import courses, lessons, modules, tasks, tests, upload
from app.routes import course_generator  # Генерация курса
from app.routes import course_structure
from app.routes import theories


Base.metadata.create_all(bind=engine)

app = FastAPI(title="NeuroLearn Course API (with HuggingChat)")

# ✅ Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Разрешаем фронтенду доступ
    allow_credentials=True,
    allow_methods=["*"],  # Разрешаем все HTTP-методы
    allow_headers=["*"],  # Разрешаем все заголовки
)

# ✅ Подключаем маршруты с префиксом /api
app.include_router(courses.router, prefix="/api", tags=["Courses"])
app.include_router(modules.router, prefix="/api", tags=["Modules"])
app.include_router(lessons.router, prefix="/api", tags=["Lessons"])
app.include_router(theories.router, prefix="/api", tags=["Theory"])
app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
app.include_router(tests.router, prefix="/api", tags=["Tests"])
app.include_router(upload.router, prefix="/api", tags=["Files"])
app.include_router(course_structure.router, prefix="/api", tags=["Course Structure"])
app.include_router(course_generator.router, prefix="/api", tags=["Course Generation"])


@app.get("/")
def root():
    return {"message": "Welcome to NeuroLearn Course API (DB + LLM Generation)"}
