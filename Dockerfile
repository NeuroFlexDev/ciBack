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
RUN pip install --no-cache-dir torch --index-url "${PYTORCH_INDEX_URL}" \
    && pip install --no-cache-dir -r requirements.txt

# Скопируем весь код внутрь контейнера
COPY . .

# Запускаем uvicorn (FastAPI), который слушает порт 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
