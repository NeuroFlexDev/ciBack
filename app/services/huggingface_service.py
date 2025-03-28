# app/services/huggingface_service.py
import os
from fastapi import HTTPException
from hugchat import hugchat
from hugchat.login import Login

def get_hugchat():
    """Возвращает инициализированный HuggingChat."""
    HF_EMAIL = os.getenv("HF_EMAIL")
    HF_PASS = os.getenv("HF_PASS")
    if not HF_EMAIL or not HF_PASS:
        raise HTTPException(500, "❌ HF_EMAIL и HF_PASS не настроены")

    login = Login(HF_EMAIL, HF_PASS)
    cookies = login.login(cookie_dir_path="./cookies/", save_cookies=True)
    chatbot = hugchat.ChatBot(cookies=cookies.get_dict())
    return chatbot
