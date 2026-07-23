# 🧠 NeuroLearn — Backend (`ciBack`)

Бэкенд-приложение на **FastAPI** для генерации и управления образовательными онлайн-курсами с помощью **HuggingChat (LLM)** и **RAG-подходов**.
Поддерживает полную цепочку от загрузки данных до генерации структуры и уроков, версионирование и семантический поиск.

---

## 🚀 Возможности

* **Управление курсами**:

  * CRUD для **курсов**, **модулей**, **уроков**, **тестов**, **заданий**, **теории**
  * Версионирование: откат к предыдущим ревизиям
* **AI-генерация**:

  * HuggingChat для генерации структуры курса, модулей и уроков
  * Теории, тесты и задания по шаблонам **Jinja2**
* **RAG-механизмы**:

  * Загрузка PDF/DOCX/TXT файлов
  * Анализ и суммаризация контента
  * **Semantic Search** на базе FAISS
* **OpenAPI** (Swagger UI) с примерами запросов
* Автоматизация: **CI/CD на GitHub Actions**, Docker Compose, staging-сервер с TLS

---

## 📦 Установка

```bash
# Клонируем репозиторий
git clone https://github.com/your-org/NeuroLearn.git
cd NeuroLearn/ciBack

# Создаём локальное виртуальное окружение
python -m venv .venv
source .venv/bin/activate # Linux / macOS
.\.venv\Scripts\Activate.ps1 # Windows PowerShell

# Устанавливаем runtime- и dev-зависимости
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

> ⚠️ Папка `mlcourse/` — это твоя локальная среда с кастомными либами.
> Git и линтеры её игнорируют, чтобы не портить окружение.

---

## ⚙️ Переменные окружения

Скопируйте безопасный шаблон в локальный `.env`, который не добавляется в Git:

```bash
cp .env.example .env # Linux / macOS
Copy-Item .env.example .env # Windows PowerShell
```

Затем замените локальные пароли и секреты. Для stage/prod передавайте значения
через переменные окружения или secret storage, не создавая `.env.stage` и
`.env.prod` в репозитории.

Access-токены имеют формат JWT и подписываются `JWT_SECRET`. Двухчастные
HMAC-токены из предыдущей реализации больше не принимаются: после обновления
пользователям необходимо войти заново.

```env
APP_HOST=0.0.0.0
APP_PORT=8000
DATABASE_URL=sqlite:///./database.db

JWT_SECRET=replace-with-a-random-local-secret
JWT_ALG=HS256
```

---

## 🗄️ Миграции базы данных

Alembic является единственным источником схемы. Импорт или запуск FastAPI не
создаёт таблицы и не выполняет DDL.

Для новой локальной базы сначала выполните:

```bash
python -m alembic upgrade head
python -m alembic current
```

После этого можно запускать приложение. В Docker Compose миграцию выполняет
отдельный одноразовый сервис `migrate`; сервис `web` стартует только после его
успешного завершения.

Предыдущая экспериментальная история миграций была заменена baseline revision
`20260723_0001`. Старую локальную базу нельзя автоматически обновлять поверх
старых revision ID:

1. Сделайте резервную копию базы или Docker volume.
2. Для одноразовой локальной среды предпочтительно создать чистую базу и
   выполнить `python -m alembic upgrade head`.
3. Если данные требуется сохранить, сначала вручную приведите и проверьте схему
   на соответствие текущим SQLAlchemy-моделям.
4. Только после такой проверки можно отметить существующую схему командой
   `python -m alembic stamp --purge 20260723_0001`.

Приложение не выполняет `stamp` автоматически.

---

## 🧪 Запуск приложения

```bash
uvicorn main:app --reload
```

После запуска:

* Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
* OpenAPI JSON: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

---

## 🐳 Docker Compose (локально + staging)

```bash
# поднять API и PostgreSQL
docker compose up -d --build
```

Docker Compose читает локальный `.env` для подстановки `POSTGRES_*`,
`JWT_SECRET` и остальных настроек. Значения из `.env.example` необходимо
заменить перед запуском.

---

## 🧪 Тестирование

```bash
# запуск всех тестов
python -m pytest -q

# smoke-тест health endpoint
python -m pytest tests/test_smoke.py::test_healthz -q
```

После запуска должен открыться `/api/healthz`:

```http
GET http://localhost:8000/api/healthz
{
  "ok": true,
  "db": true
}
```

---

## 📁 Структура проекта

```
ciBack/
├── app/
│   ├── core/
│   │   └── config.py    # pydantic-settings, все переменные из .env, профили
│   ├── repositories/    # доступ к данным, soft-delete фильтрация
│   ├── routes/          # API-роутеры
│   │   └── health.py    # health-check endpoint
│   ├── schemas/         # Pydantic Out/Create/Update
│   ├── models/          # SQLAlchemy модели (soft-delete)
│   ├── database/        # Подключение БД
│   ├── services/        # LLM, HuggingFace, генерация
│   └── prompts/         # Jinja2 шаблоны
├── tests/               # pytest-тесты
├── main.py              # Точка входа FastAPI
├── requirements.txt
├── docker-compose.yml
├── requirements-dev.txt # Зависимости локальной разработки и тестов
└── .env.example
```

---

## 📘 Примеры API

### Генерация модулей

```http
GET /api/courses/{course_id}/generate_modules
```

### Загрузка документа и обновление описания курса (RAG)

```http
POST /api/courses/{course_id}/upload-description
Content-Type: multipart/form-data
```

### Health Check

```http
GET /api/healthz
```

---

## 🗺️ Roadmap (Backend)

### ✅ Готово

* CRUD API для всех сущностей
* AI-генерация структуры и контента
* Semantic Search (FAISS)
* Загрузка и анализ файлов (RAG)
* Версионирование данных
* CI/CD на GitHub Actions
* Staging сервер с TLS
* Health endpoint и smoke-тесты
* Автоматические Alembic миграции

### 🛠️ В разработке

* Интеграция с arXiv, Semantic Scholar
* LangChain-агент для диалога с курсом
* Расширенные роли (ученик/редактор/админ)

### 🔮 В планах

* SCORM/xAPI импорт/экспорт
* WebSocket уведомления
* Расширенная аналитика и метрики

---

*Последнее обновление: 2025-21-11*

---
