# app/services/generation_service.py
import json
import traceback
from jinja2 import Environment, FileSystemLoader
from fastapi import HTTPException
from app.services.huggingface_service import get_hugchat

env = Environment(loader=FileSystemLoader("app/prompts"))

def render_prompt(template_name: str, **kwargs) -> str:
    """Рендерит шаблон промта."""
    template = env.get_template(template_name)
    return template.render(**kwargs)

def generate_from_prompt(template_name: str, **kwargs) -> dict:
    """Генерация через LLM и шаблон Jinja."""
    chatbot = get_hugchat()
    prompt = render_prompt(template_name, **kwargs)

    print(f"📤 Prompt:\n{prompt}")
    resp = chatbot.chat(prompt)
    raw = resp.wait_until_done()

    # Удаляем markdown-обертку
    raw = raw.strip().strip("```json").strip("```")
    print(f"📥 Raw JSON:\n{raw}")

    try:
        return json.loads(raw)
    except Exception:
        print("❌ Ошибка парсинга JSON:")
        print(traceback.format_exc())
        raise HTTPException(500, "Ошибка разбора JSON от LLM")
