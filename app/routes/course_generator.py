import os
import json
import traceback
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.course import Course
from app.models.module import Module
from app.models.lesson import Lesson
from app.models.test import Test
from app.models.task import Task

from app.models.course_structure import CourseStructure

from hugchat import hugchat
from hugchat.login import Login

router = APIRouter()


# Модель для получения деталей по заголовку модуля
class ModuleTitle(BaseModel):
    title: str


def get_hugchat():
    """Подключаем HuggingChat."""
    HF_EMAIL = os.getenv("HF_EMAIL")
    HF_PASS = os.getenv("HF_PASS")
    if not HF_EMAIL or not HF_PASS:
        raise HTTPException(500, "❌ HF_EMAIL/HF_PASS не настроены в переменных окружения")

    login = Login(HF_EMAIL, HF_PASS)
    cookies = login.login(cookie_dir_path="./cookies/", save_cookies=True)
    chatbot = hugchat.ChatBot(cookies=cookies.get_dict())

    return chatbot


@router.get("/generate_modules")
def generate_modules(db: Session = Depends(get_db)):
    """
    📌 Генерирует список модулей **без контента**.
    """
    try:
        chatbot = get_hugchat()

        # 1️⃣ **Загружаем данные о курсе**
        course = db.query(Course).order_by(Course.id.desc()).first()
        if not course:
            raise HTTPException(404, "❌ Данные о курсе не найдены")

        # 2️⃣ **Загружаем сохраненную структуру**
        cs = db.query(CourseStructure).first()
        if not cs:
            raise HTTPException(404, "❌ Структура курса не найдена")

        prompt = f"""
          У меня есть курс "{course.name}" ({course.level}).
          Описание курса: {course.description}.
          В нем {cs.sections} модулей.

          Сгенерируй JSON:
          {{
            "modules": [
              {{"title": "...", "description": "..."}}, ...
            ]
          }}
          Только названия модулей и их описание. Без лишнего текста – чистый JSON.
        """

        print(f"📢 Отправляем запрос в LLM: {prompt}")

        response_gen = chatbot.chat(prompt)
        raw = response_gen.wait_until_done()
        
        # Фиксим неправильный JSON
        raw = raw.strip().strip("```json").strip("```")  # Убираем Markdown-форматирование
        print(f"📢 Ответ от LLM (исправленный): {raw}")

        try:
            modules_data = json.loads(raw)
        except:
            print("❌ Ошибка парсинга JSON от LLM")
            print(traceback.format_exc())
            raise HTTPException(500, "❌ Некорректный JSON от LLM")

        if "modules" not in modules_data:
            raise HTTPException(500, "❌ LLM не вернул ключ 'modules'")

        return modules_data

    except Exception as e:
        print(f"❌ Ошибка в /generate_modules: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(500, f"❌ Ошибка генерации модулей: {str(e)}")


@router.post("/generate_module_lessons")
def generate_module_lessons(title: ModuleTitle, db: Session = Depends(get_db)):
    """
    📌 Генерация списка уроков **без детального контента**.
    """
    try:
        chatbot = get_hugchat()

        course = db.query(Course).order_by(Course.id.desc()).first()
        if not course:
            raise HTTPException(404, "❌ Данные о курсе не найдены")

        cs = db.query(CourseStructure).first()
        if not cs:
            raise HTTPException(404, "❌ Структура курса не найдена")

        prompt = f"""
          У меня есть курс "{course.name}" ({course.level}).
          Описание курса: {course.description}.

          Сейчас идет генерация модуля "{title.title}".
          В нем {cs.lessons_per_section} уроков.

          Сгенерируй JSON:
          {{
            "lessons": [{{"lesson": "...", "description": "..."}}, ...]
          }}

          Только заголовки уроков и краткое описание. ЧИСТЫЙ JSON.
        """

        print(f"📢 Отправляем запрос в LLM для модуля: {title.title}")

        resp_gen = chatbot.chat(prompt)
        raw = resp_gen.wait_until_done()

        # Убираем ` ```json` и ` ``` `
        raw = raw.strip().strip("```json").strip("```")
        print(f"📢 Ответ от LLM (исправленный): {raw}")

        try:
            data = json.loads(raw)
        except:
            print("❌ Ошибка парсинга JSON от LLM")
            print(traceback.format_exc())
            raise HTTPException(500, "❌ Некорректный JSON от LLM")

        if "lessons" not in data:
            raise HTTPException(500, "❌ LLM не вернул ключ 'lessons'")

        return data

    except Exception as e:
        print(f"❌ Ошибка в /generate_module_lessons: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(500, f"❌ Ошибка генерации уроков: {str(e)}")


import traceback

@router.post("/generate_lesson_content")
def generate_lesson_content(lesson: ModuleTitle, db: Session = Depends(get_db)):
    """
    📌 Генерация контента **для одного урока** (теория, вопросы, задания).
    """
    try:
        chatbot = get_hugchat()

        course = db.query(Course).order_by(Course.id.desc()).first()
        if not course:
            raise HTTPException(404, "❌ Данные о курсе не найдены")

        prompt = f"""
          Урок "{lesson.title}" является частью курса "{course.name}".
          Описание курса: {course.description}.

          Сгенерируй JSON:
          {{
            "theory": "...",
            "questions": [{{"question": "...", "answers": ["...", "...", "..."], "correct": "..."}}],
            "tasks": [{{"name": "...", "description": "..."}}]
          }}

          - **Теория**: Развёрнутое объяснение материала.
          - **Вопросы**: Несколько вариантов ответа, один правильный.
          - **Задания**: Практическая работа для закрепления.

          Без лишнего текста, только ЧИСТЫЙ JSON.
        """

        print(f"📢 Отправляем запрос в LLM для урока: {lesson.title}")

        resp_gen = chatbot.chat(prompt)
        raw = resp_gen.wait_until_done()

        # Убираем ` ```json` и ` ``` `
        raw = raw.strip().strip("```json").strip("```")
        print(f"📢 Ответ от LLM (исправленный): {raw}")

        try:
            data = json.loads(raw)
        except:
            print("❌ Ошибка парсинга JSON от LLM")
            print(traceback.format_exc())
            raise HTTPException(500, "❌ Некорректный JSON от LLM")

        return data

    except Exception as e:
        print(f"❌ Ошибка в /generate_lesson_content: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(500, f"❌ Ошибка генерации урока: {str(e)}")

@router.post("/save_modules")
def save_modules(data: dict, db: Session = Depends(get_db)):
    """Сохраняем список модулей в БД."""
    try:
        print("📥 Данные для сохранения:", data)  # Debugging log
        
        course = db.query(Course).order_by(Course.id.desc()).first()
        if not course:
            raise HTTPException(404, "❌ Курс не найден")

        db.query(Module).filter(Module.course_id == course.id).delete()

        for mod in data["modules"]:
            module = Module(
                course_id=course.id,
                title=mod["title"],
            )
            db.add(module)
            db.commit()  # Фиксируем, чтобы получить module.id
            
            if "lessons" in mod:
                for lesson in mod["lessons"]:
                    new_lesson = Lesson(
                        module_id=module.id,
                        title=lesson.get("lesson", "Без названия"),
                        description=lesson.get("description", ""),
                    )
                    db.add(new_lesson)

            if "tests" in mod:
                for test in mod["tests"]:
                    if "test" not in test or "description" not in test:
                        raise HTTPException(400, f"❌ Ошибка в тесте: {test}")

                    # 🛠 Разбираем `description` на `answers` и `correct`
                    desc = test["description"]
                    if "Варианты:" in desc and "(Правильный:" in desc:
                        parts = desc.split("Варианты:")[1].split("(Правильный:")
                        answers = parts[0].strip().split(", ")
                        correct = parts[1].replace(")", "").strip()
                    else:
                        raise HTTPException(400, f"❌ Ошибка в формате теста: {test}")

                    new_test = Test(
                        module_id=module.id,
                        question=test["test"],
                        answers=json.dumps(answers),  # JSON-массив ответов
                        correct_answer=correct,
                    )
                    db.add(new_test)

            if "tasks" in mod:
                for task in mod["tasks"]:
                    if "name" not in task:
                        raise HTTPException(400, f"❌ Ошибка в задании: {task}")

                    new_task = Task(
                        module_id=module.id,
                        name=task["name"],
                        description=task.get("description", ""),
                    )
                    db.add(new_task)

        db.commit()
        return {"message": "✅ Модули успешно сохранены"}
    
    except Exception as e:
        db.rollback()
        print("❌ Ошибка сохранения:", str(e))  # Debugging line
        raise HTTPException(500, f"❌ Ошибка сохранения: {str(e)}")


@router.get("/load_modules")
def load_modules(db: Session = Depends(get_db)):
    """Загружаем модули из БД."""
    try:
        course = db.query(Course).order_by(Course.id.desc()).first()
        if not course:
            raise HTTPException(404, "❌ Курс не найден")

        modules = db.query(Module).filter(Module.course_id == course.id).all()
        if not modules:
            return {"modules": []}

        return {
            "modules": [
                {
                    "title": mod.title,
                    "lessons": [
                        {
                            "lesson": lesson.title,
                            "description": lesson.description,
                        }
                        for lesson in mod.lessons
                    ],
                    "tests": [
                        {
                            "test": test.question,
                            "answers": json.loads(test.answers),
                            "correct": test.correct_answer,
                        }
                        for test in mod.tests
                    ],
                    "tasks": [
                        {
                            "name": task.name,
                            "description": task.description,
                        }
                        for task in mod.tasks
                    ],
                }
                for mod in modules
            ]
        }
    
    except Exception as e:
        print("❌ Ошибка загрузки модулей:", str(e))  # Debugging
        raise HTTPException(500, f"❌ Ошибка загрузки модулей: {str(e)}")