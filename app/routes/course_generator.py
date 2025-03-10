import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List

from hugchat import hugchat
from hugchat.login import Login

from app.database.db import get_db
from app.models.course_structure import CourseStructure

router = APIRouter()

# Модель для приёма модульного запроса
class ModuleTitle(BaseModel):
    title: str

@router.post("/generate_content_overview")
def generate_content_overview(db: Session = Depends(get_db)):
    """
    Генерирует список модулей (только заголовки, к примеру)
    на основе сохраненной структуры.
    Возвращает JSON вида { "modules": [ { "title": "...", "description": "..." }, ... ] }
    """
    HF_EMAIL = os.getenv("HF_EMAIL")
    HF_PASS = os.getenv("HF_PASS")
    if not HF_EMAIL or not HF_PASS:
        raise HTTPException(500, "HF_EMAIL/HF_PASS not set")

    # Ищем структуру
    cs = db.query(CourseStructure).first()
    if not cs:
        raise HTTPException(404, "Структура не найдена")

    # Авторизация
    login = Login(HF_EMAIL, HF_PASS)
    cookies = login.login(cookie_dir_path="./cookies/", save_cookies=True)
    chatbot = hugchat.ChatBot(cookies=cookies.get_dict())

    # Пример промпта, который вернет только "modules"
    prompt = f"""
      У меня есть курс с {cs.sections} секциями.
      Тестов на секцию: {cs.tests_per_section},
      Уроков на секцию: {cs.lessons_per_section},
      Финальный тест: {cs.final_test}.
      Сгенерируй JSON вида:
      {{
        "modules": [
          {{"title": "...", "description": "..."}},
          ...
        ]
      }}
      Только названия модулей и краткое описание. На выходе выдавай ИСКЛЮЧИТЕЛЬНО JSON. БОЛЬШЕ НИКАКИХ ЛИШНИХ СИМВОЛОВ, ТОЛЬКО ЧТОБЫ РЕЗУЛЬТАТ МОЖНО БЫЛО ОФОРМИТЬ КАК json.loads().
    """

    response_gen = chatbot.chat(prompt)
    raw = response_gen.wait_until_done()

    

    import json
    try:
        data = json.loads(raw)
    except:
        raise HTTPException(500, "Некорректный JSON от LLM")

    if "modules" not in data:
        raise HTTPException(500, "LLM не вернул ключ 'modules'")

    return data


@router.post("/generate_module_details")
def generate_module_details(title: ModuleTitle, db: Session = Depends(get_db)):
    """
    Генерирует уроки/тесты/задачи для конкретного модуля (по заголовку).
    Возвращает JSON вида:
    {
      "lessons": [ { "lesson": "...", "description": "..." }, ... ],
      "tests": [ { "test": "...", "description": "..." }, ... ],
      "tasks": [ { "name": "..." }, ... ]
    }
    """
    HF_EMAIL = os.getenv("HF_EMAIL")
    HF_PASS = os.getenv("HF_PASS")
    if not HF_EMAIL or not HF_PASS:
        raise HTTPException(500, "HF_EMAIL/HF_PASS not set")

    login = Login(HF_EMAIL, HF_PASS)
    cookies = login.login(cookie_dir_path="./cookies/", save_cookies=True)
    chatbot = hugchat.ChatBot(cookies=cookies.get_dict())

    prompt = f"""
      У меня есть модуль с названием '{title.title}'.
      Сгенерируй JSON вида:
      {{
        "lessons": [{{"lesson":"...", "description":"..."}}, ...],
        "tests": [{{"test":"...", "description":"..."}}, ...],
        "tasks": [{{"name":"..."}}]
      }}      

      На выходе выдавай ИСКЛЮЧИТЕЛЬНО JSON. БОЛЬШЕ НИКАКИХ ЛИШНИХ СИМВОЛОВ, ТОЛЬКО ЧТОБЫ РЕЗУЛЬТАТ МОЖНО БЫЛО ОФОРМИТЬ КАК json.loads(). БЕЗ СИМВОЛОВ `.
    """

    resp_gen = chatbot.chat(prompt)
    raw = resp_gen.wait_until_done()

    print(raw)
    import json
    try:
        data = json.loads(raw)
    except:
        raise HTTPException(500, "LLM вернул некорректный JSON для детализации модуля")

    # Проверяем ключи
    for key in ["lessons", "tests", "tasks"]:
        if key not in data:
            data[key] = []  # Если не вернуло, пусть будет пустой

    return data
