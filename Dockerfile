# Используем легкий Python образ
FROM python:3.10-slim

# Указываем рабочую директорию в контейнере
WORKDIR /app

# Скопируем файл зависимостей
COPY requirements.txt .

# Установим зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Скопируем весь код внутрь контейнера
COPY . .

# Запускаем uvicorn (FastAPI), который слушает порт 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
