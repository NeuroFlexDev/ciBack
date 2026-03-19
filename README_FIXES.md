# README Fixes

## Что нужно исправить обязательно

- Заменить инструкцию про `.env` на фактическую схему профилей.
  Сейчас код читает `.env.dev/.env.stage/.env.prod`, а не `.env`: `app/core/config.py:4`, `app/core/config.py:30-32`.

- Уточнить, что дефолтный профиль без внешнего `ENV` это `dev`.
  Значение `ENV` внутри `.env.stage` не участвует в выборе файла, потому что профиль вычисляется до загрузки env-файла.

- Убрать из README переменные `HF_EMAIL` и `HF_PASS`.
  Реальный код использует `HF_TOKEN`: `app/services/hf_infer_service.py:99-120`, `app/services/huggingface_service.py:9-13`.

- Добавить в README реальные переменные для GigaChat.
  Нужны `GIGA_CLIENT_ID`, `GIGA_CLIENT_SECRET`, опционально `GIGA_SCOPE`: `app/services/gigachat_service.py:57-60`, `app/services/gigachat_service.py:102-128`.

- Исправить описание auth endpoint’ов.
  Сейчас фактические пути `/api/register`, `/api/login`, `/api/me`, `/api/change-password`: `app/routes/auth.py:12-37`.
  В `OAuth2PasswordBearer` указан несуществующий `tokenUrl="/api/auth/login"`: `app/routes/auth.py:10`.

- Исправить раздел про Docker Compose.
  README говорит про Postgres + healthcheck: `README.md:80-89`.
  По факту:
  `docker-compose.yml` healthcheck не содержит,
  `web` использует `.env.dev`,
  `.env.dev` держит SQLite URL, а не Postgres: `docker-compose.yml:21-24`, `.env.dev:4-5`.

- Исправить контракт `/api/healthz`.
  README обещает `{"ok": true, "db": true}`: `README.md:102-109`.
  Реальный endpoint возвращает только `{"ok": true}`: `app/routes/healthz.py:9-11`.
  DB-check сейчас вынесен в `/api/readiness`: `app/routes/healthz.py:13-22`.

- Убрать или понизить claim про “автоматические Alembic миграции”.
  В текущем коде на старте вызывается `create_all()`: `main.py:24`,
  а `alembic upgrade head` не проходит.

- Убрать или понизить claim про “версионирование данных / откат”.
  Версионные endpoint’ы сейчас падают, а создание ревизий не реализовано: `app/routes/versioning.py:12-38`.

- Убрать или понизить claim про “Semantic Search (FAISS) готов”.
  Search router не смонтирован в app, upload не индексирует документы: `app/routes/search.py:9-11`, `main.py:57-70`, `app/services/upload_service.py:64-85`.

- Убрать или понизить claim про “staging сервер с TLS”.
  В репозитории нет TLS-конфигурации, reverse proxy или deployment manifests.

- Исправить раздел “Тестирование”.
  README утверждает, что `pytest -q` проходит, но фактически suite падает на `tests/test_external_sources.py::test_aggregated_search_empty`.

- Исправить раздел “Структура проекта”.
  Там упомянут `routes/health.py`, а фактический файл называется `app/routes/healthz.py`.

- Уточнить, что `README.md` сейчас не совпадает с реальным состоянием stage/prod.
  В `.env.stage` и `.env.prod` указан `postgresql+asyncpg`, но runtime настроен на sync SQLAlchemy и не содержит `asyncpg`.

- Исправить дату последнего обновления.
  `README.md:190` содержит невалидный формат `2025-21-11`.
