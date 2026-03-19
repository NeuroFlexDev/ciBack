# Optional Implementation Tasks

Ниже ваш список из 23 инициатив, но уже с фактическим статусом по репозиторию.

| ID | Инициатива | Текущее состояние | Подтверждение | Следующий шаг | Рекомендуемый приоритет |
|----|------------|-------------------|---------------|---------------|--------------------------|
| 1 | Разложить код по слоям и убрать God-модули | Частично | Папки `routes/`, `schemas/`, `services/`, `repositories/`, `core/` уже есть; но есть смешение логики в `app/routes/generation.py`, `app/routes/versioning.py`, `app/routes/agent.py` | Вынести use-case слой для generation/upload/versioning, убрать dead code | P1 |
| 2 | Единый config на `pydantic-settings` с профилями | Частично | `app/core/config.py:6-35` уже использует `BaseSettings` | Исправить profile selection, добавить все реальные поля и валидацию, синхронизировать env-файлы | P0 |
| 3 | Единый стиль моделей БД и базовый mixin | Частично | `app/models/base.py:4-8` добавляет `id/created_at/updated_at/is_deleted` | Довести миграции до этого состояния и проверить все модели/связи/индексы | P1 |
| 4 | Донастроить Alembic и прогонять миграции в Docker/CI | Не реализовано | `main.py:24`, `migrations/versions/*.py`, `docker-compose.yml`, `.github/workflows/ci.yml` | Пересобрать migration chain, добавить migration step в CI/Compose | P0 |
| 5 | Чёткие Request/Response модели для всех публичных endpoints | Частично | Pydantic схемы есть в `app/schemas/*` | Исправить сломанные response-модели и убрать raw ORM/сырой возврат из versioning | P0 |
| 6 | Централизованный логгер со структурой и request_id | Частично | `log.py:11-40`, `main.py:38-54` | Убрать утечки URL/PII, стандартизовать формат и уровни | P1 |
| 7 | Полноценный `/api/healthz` c DB/vector/file/queue checks | Частично | `app/routes/healthz.py:9-22` | Расширить проверки и развести liveness/readiness | P1 |
| 8 | Расширить unit/API тесты CRUD | Частично | Есть 14 тестов, покрыты courses/modules/lessons/basic generation | Добавить theory/task/test/course_structure/auth/versioning/upload | P0 |
| 9 | Интеграционные тесты ключевых сценариев RAG | Не реализовано | В `tests/` нет сценария `upload -> index -> search -> generate` | Добавить 1-2 сквозных integration tests | P0 |
| 10 | Отдельный сервисный слой LLM с retry/timeout/latency/model config | Частично | `app/services/generation_service.py`, `app/services/hf_infer_service.py`, `app/services/gigachat_service.py` | Нормализовать адаптеры, policy ошибок и логи | P1 |
| 11 | Очередь фоновых задач для тяжёлых операций | Не реализовано | Repo-wide search по `celery|rq|arq|BackgroundTasks|redis` пустой | Выбрать Arq/RQ/Celery и вынести generation/indexing | P2 |
| 12 | Нормальный RAG-пайплайн с chunking/metadata/persistent FAISS | Частично | `app/services/embedding_service.py:8-23`, `app/services/upload_service.py:64-85` | Добавить chunking, course metadata, persistence и прогрев индекса | P0 |
| 13 | RAG v2: дополнить описание курса и создавать курс из корпуса | Не реализовано | Есть только summary update текущего курса | После P0 сделать mode `create course from corpus` | P2 |
| 14 | QA-чат по содержимому курса с историей | Частично | `app/routes/chat.py`, `app/services/chat_service.py`, `app/chat_engine/lc_engine.py` | Добавить course context, persistence и RAG retrieval | P1 |
| 15 | Роли и права (student/L&D/editor/admin) | Не реализовано | Нет role tables и guards | Для пилота ввести owner/editor/admin minimal model | P1 |
| 16 | Усилить JWT auth: access/refresh, rotation, TTL, env-only secrets | Частично | JWT/login есть: `app/routes/auth.py`, `app/services/auth.py` | Исправить secret source, добавить refresh/rotation и защиту write-endpoints | P0 |
| 17 | Rate limiting и anti-abuse для LLM | Не реализовано | Repo-wide search по limiter/rate-limit пустой | Добавить user/course quotas после auth stabilization | P2 |
| 18 | Storage abstraction (disk / S3 / MinIO) | Не реализовано | Upload сейчас работает через temp local file: `app/services/upload_service.py:69-85` | Ввести storage interface и адаптеры | P2 |
| 19 | Compliance/PII masking | Не реализовано | Repo-wide search по `PII|mask|compliance` пустой | Добавить preprocessing policy и audit flags | P2 |
| 20 | SCORM/xAPI экспорт | Не реализовано | Repo-wide search по `SCORM|xAPI` пустой | Дизайн export service и артефактов | P2 |
| 21 | Метрики и трейсинг | Не реализовано | Repo-wide search по `prometheus|opentelemetry|metrics|instrument` пустой | Добавить Prometheus + request/LLM metrics | P2 |
| 22 | OpenAPI-документация с тегами, примерами, описаниями | Частично | Теги частично заданы в `main.py:57-70`, `app/routes/*` | Добавить examples и синхронизировать реальные контракты | P1 |
| 23 | `ROADMAP.md` и `CONTRIBUTING.md` | Не реализовано в исходном repo | Отсутствуют в корне репозитория | Подготовить документы на базе аудита | P1 |
