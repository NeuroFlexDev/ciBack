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

### Canvas курса

Canvas хранится в БД как неизменяемые версии. Если canvas ещё не создан,
`GET` возвращает версию `0` и пустые массивы. В `PUT` поле `version` — версия,
которую клиент прочитал последней; сервер создаёт следующую версию. Если canvas
успел изменить другой запрос, API отвечает `409 Conflict`.

```http
GET /api/courses/{course_id}/canvas

PUT /api/courses/{course_id}/canvas
Content-Type: application/json

{
  "version": 0,
  "nodes": [{"id": "module-1", "position": {"x": 0, "y": 0}}],
  "edges": []
}
```

История версий доступна отдельно:

```http
GET /api/courses/{course_id}/canvas/versions?limit=20&offset=0
GET /api/courses/{course_id}/canvas/versions/{version}
```

Первый endpoint возвращает только metadata в порядке от новой версии к старой,
не передавая тяжёлые `nodes`/`edges`. Второй возвращает полный immutable
snapshot. Поле `is_current` показывает версию из `courses.current_graph_id`.

Узлы должны иметь уникальные строковые `id`. Поля `source` и `target` каждой
связи должны указывать на существующие узлы. Дополнительные поля React Flow
сохраняются.

#### Как frontend должен отказаться от localStorage

Backend уже является источником истины, но он не может удалить или перестать
читать `localStorage` внутри браузерного приложения. Это требует отдельного
frontend PR:

1. При открытии редактора вызвать `GET /api/courses/{course_id}/canvas`.
2. Сохранить полученные `nodes`, `edges` и `version` в состоянии компонента.
3. Несохранённые изменения держать только в памяти frontend.
4. При сохранении вызвать `PUT`, передав последнюю полученную `version`.
5. После успеха заменить локальную version значением из ответа.
6. При `409 Conflict` повторно загрузить active canvas и показать разрешение
   конфликта, не перезаписывая серверную версию автоматически.
7. Историю загружать metadata endpoint, а выбранный snapshot — detail endpoint.
8. После rollout удалить `localStorage.getItem/setItem` для canvas и очистить
   старые canvas-ключи.

Пока эти изменения не внесены во frontend, refresh и работа на другом устройстве
могут по-прежнему зависеть от его старой логики, даже если backend persistence
полностью готов.

### Документы курса и локальное файловое хранилище

Step 2 wizard работает с уже созданным draft-курсом. Полный сценарий:

1. Создать draft через `POST /api/courses/drafts` и сохранить его `id`.
2. Загрузить каждый источник через `POST /api/courses/{course_id}/documents`.
3. Для принятого документа вызвать `POST /api/documents/{document_id}/reindex`.
4. Читать состояние через список или `GET /api/documents/{document_id}`.

```http
POST /api/courses/{course_id}/documents
Content-Type: multipart/form-data

GET /api/courses/{course_id}/documents?limit=20&offset=0&sort_by=created_at&sort_order=desc
GET /api/documents/{document_id}
POST /api/documents/{document_id}/reindex
```

Multipart-поле всегда называется `file`. Пример:

```bash
curl -X POST "http://localhost:8000/api/courses/42/documents" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F "file=@./regulation.pdf;type=application/pdf"
```

Успешная загрузка возвращает `202 Accepted`: файл сохранён, но ещё не считается
проиндексированным. Upload не запускает ненадёжную in-process очередь. До
появления штатного worker обработка явно запускается endpoint-ом `/reindex`.

```json
{
  "id": 17,
  "course_id": 42,
  "original_filename": "regulation.pdf",
  "content_type": "application/pdf",
  "size_bytes": 248371,
  "source_type": "upload",
  "version": 1,
  "status": "processing",
  "error_message": null,
  "created_at": "2026-07-31T12:10:00",
  "updated_at": "2026-07-31T12:10:00"
}
```

#### Форматы и валидация

| Формат | Расширение | MIME type | Проверка содержимого |
|---|---|---|---|
| PDF | `.pdf` | `application/pdf` | начало файла `%PDF-` |
| DOCX | `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | ZIP с `[Content_Types].xml` и `word/document.xml` |
| TXT | `.txt` | `text/plain` | корректный UTF-8/UTF-8 BOM, без NUL-байтов |

Расширение и MIME должны соответствовать друг другу. Пустой файл, неверная
сигнатура, повреждённый DOCX, бинарный или невалидный UTF-8 TXT отклоняются до
parsing, LLM и индексации. Клиентское имя очищается и хранится только как
metadata; storage key строится из owner ID, course ID и случайного UUID. SHA-256
считается во время потоковой записи.

#### Размер файла

Основная настройка задаётся в мегабайтах и по умолчанию равна 25 MiB:


```dotenv
UPLOAD_DIR=./uploads
MAX_DOCUMENT_SIZE_MB=25
```

Лимит переводится в байты как `MB * 1024 * 1024`. Запись выполняется chunks по
1 MiB и прекращается сразу после превышения лимита; произвольно большой файл не
читается целиком в память перед проверкой. Старый `MAX_UPLOAD_BYTES` поддержан
как legacy override для существующих deployment-конфигураций, но в новых `.env`
используется `MAX_DOCUMENT_SIZE_MB`.

#### Публичные статусы и список

Frontend видит только стабильные значения:

| Внутренний статус | Публичный статус |
|---|---|
| `uploaded`, `queued`, `processing` | `processing` |
| `indexed` | `ready` |
| `failed` | `error` |

Фильтр `status` принимает только `processing`, `ready` или `error`; один фильтр
`processing` включает все три внутренних промежуточных состояния. Поддерживаются
`limit` 1–100, `offset`, `source_type`, `sort_by` и `sort_order=asc|desc`.
Ответ списка содержит `items`, `total`, `limit`, `offset`. Поля `storage_key`,
`content_hash`, chunks, embedding IDs и внутренние статусы наружу не выдаются.
При ошибке обработки `error_message` содержит безопасное сообщение без stack
trace, локальных путей и credentials.

#### Ошибки и ACL

| HTTP | Причина |
|---:|---|
| `400` | multipart-файл отсутствует, пуст или повреждён |
| `401` | отсутствует или недействителен access token |
| `404` | курс/документ отсутствует, удалён или принадлежит другому owner |
| `413` | превышен настроенный размер |
| `415` | расширение или MIME не поддерживаются/не совпадают |
| `422` | некорректные query-параметры |
| `500` | непредвиденная ошибка без раскрытия внутренних деталей |

Documents API использует owner ACL. Для чужого ресурса возвращается `404`, а не
`403`, чтобы не раскрывать факт его существования. Tenant/organization-фильтр
будет добавлен вместе со штатной multi-tenant моделью.

При обычном локальном запуске файлы находятся в `./uploads`. Эта папка
игнорируется Git и не должна коммититься. В Docker Compose используется
отдельный named volume `uploads_data`, смонтированный в `/data/uploads`.
Остановка контейнеров не удаляет volume, но команда `docker compose down -v`
удалит его вместе с файлами и PostgreSQL volume. Для важных локальных данных
нужно отдельно делать backup volume.

Текущий этап намеренно использует `LocalFileStorage`, однако бизнес-логика
работает через интерфейс `FileStorage`. Технический roadmap требует Storage
Adapter, но не назначает S3 или MinIO отдельным обязательным этапом. Для
production можно добавить `S3FileStorage` и выбрать его через конфигурацию:
существующие API и значение `Document.storage_key` при этом менять не нужно.
До реализации такого adapter S3/MinIO не запускаются, а Docker Compose не
содержит объектного хранилища.

### Индексация и ACL-aware retrieval

```http
POST /api/documents/{document_id}/reindex
GET  /api/courses/{course_id}/retrieval?q=архитектура&limit=5
```

Reindex сохраняет chunks и полный retrieval scope (`document/version`,
`page/section`, source, owner, organization и course). Ответ retrieval содержит
текст найденного фрагмента и citation с документом, страницей/секцией и chunk
ID. Кандидаты выбираются из БД только среди текущих проиндексированных
документов владельца курса до выполнения vector search; чужие и старые версии
не попадают в выборку.

`FaissVectorStore` остаётся локальной in-memory demo-реализацией за интерфейсом
`VectorStore`: после рестарта его нужно заполнить повторным reindex. Он не
является production source of truth и не подходит для нескольких workers.
Следующий P2 backend (`PgVectorStore`) сможет заменить его без изменения
retrieval API. До появления штатного worker reindex выполняется синхронно, а
его состояние и ошибки сохраняются в `GenerationRun`.

### Step 3: настройки генерации курса

После загрузки документов wizard сохраняет всю форму Step 3 одним идемпотентным
PUT. GET восстанавливает форму после refresh или на другом устройстве:

```http
PUT /api/courses/{course_id}/generation-settings
GET /api/courses/{course_id}/generation-settings
```

```json
{
  "title": "Введение в информационную безопасность",
  "goal": "Научить сотрудников соблюдать базовые требования",
  "target_audience": "Новые сотрудники",
  "difficulty": "basic",
  "language": "ru",
  "lesson_count": 12,
  "module_tests_enabled": true,
  "final_test_enabled": true
}
```

Поля `title`, `goal`, `difficulty`, `language`, `lesson_count` и оба boolean
обязательны; неизвестные поля запрещены. `title` обрезается по краям и должен
содержать 1–200 символов, `goal` — 1–2000. `target_audience` допускает `null`,
ограничен 1000 символами, а строка из пробелов нормализуется в `null`.

`difficulty` принимает только `internship`, `basic`, `intermediate`, `advanced`;
`language` — только `ru` или `en`. `lesson_count` находится в диапазоне 1–100
и означает суммарное число уроков во всём курсе, а не число уроков в модуле.

Title хранится в `Course`, остальные значения — в единственной
`CourseGenerationSettings` для курса. Повторный PUT обновляет ту же запись.
Успешное сохранение переводит курс в `configured`. Настройки разрешено менять у
`draft`, `configured`, `generation_failed` и `ready`; готовый курс при этом снова
становится `configured`, но существующий graph не удаляется. Во время
`generating` PUT возвращает `409`.

Если GET вызывается до сохранения формы, API отвечает `404`:

```json
{
  "detail": {
    "code": "generation_settings_not_found",
    "message": "Настройки генерации курса не найдены"
  }
}
```

Owner ACL скрывает чужой или удалённый курс через `404`. Ошибки enum, диапазона,
пробельных обязательных строк, отсутствующих boolean и лишних полей дают `422`.

#### Асинхронный запуск генерации (Step 3 → Step 4)

Production-запуск выполняется через `POST /api/courses/{course_id}/generation-runs`.
Запрос содержит сохранённую схему `settings` и список `document_ids`; принимаются
только проиндексированные документы текущего владельца и курса. API сохраняет
`GenerationRun` и immutable snapshots настроек/версий документов, ставит задачу
в Redis/RQ и сразу отвечает `202 Accepted` с `run_id` и `status_url`.

API, Redis, миграции и отдельный worker запускаются вместе:

```bash
docker compose up --build db redis migrate web worker
```

Локально worker можно запустить отдельно (при доступных PostgreSQL и Redis):

```bash
rq worker generation --url redis://localhost:6379/0
```

`JOB_EAGER=true` предназначен только для локальных/тестовых запусков. В production
FastAPI `BackgroundTasks` не используется: queued job переживает процесс API.

Step 4 polls persisted progress through `GET /api/generation-runs/{run_id}`.
The complete response schema, checkpoints, safe errors and recommended polling
interval are in `docs/contracts/generation_progress.md`.
`POST /api/generation-runs/{run_id}/retry` creates a separate run from immutable
snapshots and preserves the failed attempt history.

#### Как настройки влияют на generate-graph

`POST /api/courses/{course_id}/generate-graph` читает настройки из БД, поэтому
frontend не отправляет их повторно. Перед запуском требуются сохранённые settings
и хотя бы один документ в публичном состоянии `ready` (`indexed` внутри).

Каждый `GenerationRun` получает immutable `settings_snapshot`. Snapshot вместе
с версиями и hash документов входит в fingerprint: изменение формы создаёт новый
run даже при прежних документах. Курс переходит `configured → generating`, затем
в `ready` при успехе или `generation_failed` при ошибке.

Goal и target audience задают контекст prompt, difficulty — глубину, language —
язык, а LLM обязан вернуть ровно `lesson_count` lesson-узлов. Несовпадение числа
уроков считается ошибкой генерации. Module test после каждого модуля и final
test добавляются сервером детерминированно согласно boolean-полям, а не оставлены
на усмотрение LLM.

### Генерация модулей

```http
GET /api/courses/{course_id}/generate_modules
```

### Загрузка документа и обновление описания курса (RAG)

Legacy endpoint ниже помечен deprecated и не используется новым wizard flow:

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
### Step 5: generated course structure

После завершения generation run нормализованная структура доступна через
`GET /api/courses/{course_id}/structure`, а содержимое отдельного модуля — через
`GET /api/modules/{module_id}`. Модули, уроки и вопросы возвращаются в явном
порядке; метрики и оценка длительности рассчитываются backend из persisted данных.
Полный локальный контракт описан в `docs/contracts/course_review_structure.md`.

Редактор использует обязательный `expected_revision` для PUT, DELETE и batch
reorder. Конфликт возвращает HTTP 409 и не перезаписывает параллельные изменения.
После реализации публикации в этапе 10.8 первая правка опубликованного курса
должна создавать новую draft revision; `publication_status` намеренно не добавлен
в 10.7.

### Публикация курса

Готовый курс публикуется идемпотентным запросом `POST /api/courses/{course_id}/publish`.
Workflow генерации (`status`) и состояние публикации (`publication_status`) хранятся
раздельно. Публикация разрешена владельцу только для `ready`-курса с активным модулем,
уроком и непустым содержимым урока; активная или частичная генерация блокирует операцию.
При публикации сохраняется полный versioned snapshot структуры. Первая последующая
правка открывает новую draft revision, которую можно опубликовать повторно.

`GET /api/courses/` поддерживает `publication_status=draft|published`, `limit` и
`offset`. Ответ остаётся JSON-массивом для обратной совместимости, а `X-Total-Count`,
`X-Limit` и `X-Offset` содержат метаданные пагинации. Каждый элемент включает
`publication_status`, `module_count`, `lesson_count`, `updated_at` и `published_at`.
