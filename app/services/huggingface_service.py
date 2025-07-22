# app/services/hf_infer_service.py
import os

from fastapi import HTTPException
from huggingface_hub import InferenceClient


def get_hf_client():
    token = os.getenv("HF_TOKEN")
    model = os.getenv("HF_MODEL", "mistralai/Mixtral-8x7B-Instruct-v0.1")
    if not token:
        raise HTTPException(500, "HF_TOKEN не настроен")
    return InferenceClient(model=model, token=token)
