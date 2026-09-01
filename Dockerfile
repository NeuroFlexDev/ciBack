# Используем легкий Python образ
FROM python:3.11-slim

# Указываем рабочую директорию в контейнере
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Скопируем файл зависимостей
COPY requirements.txt .

# sentence-transformers depends on PyTorch. Install the CPU wheel explicitly so
# a backend image does not pull several gigabytes of unused CUDA libraries.
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
# Keep packaging helpers patched in the runtime image; they are inspected by image scanners.
RUN pip install --no-cache-dir torch --index-url "${PYTORCH_INDEX_URL}" \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade --force-reinstall \
        "setuptools==84.0.0" "wheel>=0.46.2" "jaraco.context>=6.1.0"

# Скопируем весь код внутрь контейнера
COPY . .

# Запускаем uvicorn (FastAPI), который слушает порт 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/healthz', timeout=5)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
