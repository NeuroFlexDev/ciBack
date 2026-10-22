# Local MathJax + resvg SVG normalization, pinned dependencies, no browser.
FROM node:24-bookworm-slim AS pdf_math
WORKDIR /pdf_runtime
COPY pdf_runtime/package.json pdf_runtime/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force

# Используем легкий Python образ
FROM python:3.11-slim

# Указываем рабочую директорию в контейнере
WORKDIR /app
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng libcairo2-dev pkg-config build-essential libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Скопируем файл зависимостей
COPY requirements.txt .

# The default deployment uses VSELLM embeddings; no CUDA/PyTorch download is needed.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade --force-reinstall \
        "setuptools==84.0.0" "wheel>=0.46.2" "jaraco.context>=6.1.0"

COPY --from=pdf_math /usr/local/bin/node /usr/local/bin/node
COPY --from=pdf_math /pdf_runtime/node_modules /app/pdf_runtime/node_modules

# Скопируем весь код внутрь контейнера
COPY . .

# Check the native SVG module in the final Linux image, not only the Node stage.
RUN node -e "const {Resvg}=require('./pdf_runtime/node_modules/@resvg/resvg-js');new Resvg('<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1\" height=\"1\"/>',{font:{loadSystemFonts:false}}).toString()"

# Запускаем uvicorn (FastAPI), который слушает порт 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/healthz', timeout=5)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
