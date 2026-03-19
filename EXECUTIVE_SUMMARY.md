# Executive Summary

## Вердикт

Backend в текущем состоянии **не готов к пилоту**.

Минимальный путь до pilot-ready состояния существует, но сначала нужно стабилизировать базовые вещи: конфигурацию, БД/миграции, публичные API-контракты, безопасность и end-to-end RAG-поток.

## Что реально работает

- FastAPI-приложение и базовая маршрутизация есть: `main.py:26-73`.
- CRUD по курсам, модулям и урокам реализован и покрыт базовыми API-тестами: `app/routes/courses.py`, `app/routes/modules.py`, `app/routes/lessons.py`, `tests/test_courses_api.py`, `tests/test_modules_api.py`, `tests/test_lessons_api.py`.
- Базовый LLM-слой и генерация модулей присутствуют: `app/services/generation_service.py:37-245`, `app/routes/generation.py:18-52`.
- Загрузка PDF/DOCX/TXT и суммаризация документа есть в заготовочном виде: `app/services/upload_service.py:48-85`.
- Smoke-check `GET /` и `GET /api/readiness` проходят локально при принудительном офлайн-режиме Hugging Face.

## Главные блокеры пилота

- Конфигурация и профили окружения не детерминированы.
  Дефолтно читается `.env.dev`, а не `.env`: `app/core/config.py:4`, `app/core/config.py:30-32`.
  `docker-compose` тоже использует `.env.dev`: `docker-compose.yml:21-22`.
  При этом `README.md:51-63` описывает другой сценарий.

- Docker Compose поднимает Postgres, но приложение внутри Compose остаётся на SQLite.
  В `.env.dev` активен `DATABASE_URL=sqlite:///./database.db`, а Postgres URL закомментирован: `.env.dev:4-5`.
  Это противоречит `README.md:80-89`.

- Stage/prod профили не поднимаются.
  В `.env.stage:4` и `.env.prod:4` указан `postgresql+asyncpg://...`, но код создаёт синхронный engine через `create_engine`: `app/database/db.py:9`.
  Фактическая проверка импорта с `ENV=stage` и `ENV=prod` завершилась ошибкой `ModuleNotFoundError: No module named 'asyncpg'`.

- Жизненный цикл БД сломан конфликтом `create_all()` и Alembic.
  На старте выполняется `Base.metadata.create_all(bind=engine)`: `main.py:24`.
  Одновременно заявлены Alembic-миграции: `migrations/`.
  Команда `venv\\Scripts\\python -m alembic upgrade head` падает с `table courses already exists`.
  В `database.db` таблица `alembic_version` есть, но версия не зафиксирована.

- Публичные API-контракты частично нерабочие.
  `TheoryResponse`, `TaskResponse`, `TestResponse`, `CourseStructureResponse` требуют `is_deleted`: `app/schemas/theory.py:10-13`, `app/schemas/task.py:11-16`, `app/schemas/test.py:13-20`, `app/schemas/course_structure.py:35-43`.
  Но роуты возвращают payload без этого поля: `app/routes/theories.py:14`, `app/routes/tasks.py:14`, `app/routes/tests.py:15-20`, `app/routes/course_structure.py:26-34`.
  Ручная проверка TestClient 2026-03-19 воспроизвела ошибки на `/api/theories/`, `/api/modules/{id}/tasks/`, `/api/course-structure/`.

- Версионирование фактически не реализовано.
  Роуты версий фильтруют по полям, которых нет в моделях: `app/routes/versioning.py:22-38`, `app/models/module_version.py:10-15`, `app/models/lesson_version.py:10-15`.
  Ручная проверка `/api/versions/module/{id}` и `/api/versions/lesson/{id}` дала `AttributeError`.
  Создание снапшотов/ревизий в коде не найдено.

- Безопасность не готова к пилоту.
  JWT подписывается хардкодом `CHANGE_ME`, а не `JWT_SECRET` из env: `app/services/security.py:7-26`.
  В репозитории закоммичены секреты и токены: `.env.dev:7,24`, `.env.stage:6,14`, `.env.prod:6,14`.
  Большинство CRUD/AI/upload endpoints не защищены авторизацией: `main.py:57-68`, маршруты не используют `Depends(get_current_user_dep)`.
  `log.py:3` печатает `DATABASE_URL` в stdout при импорте.

- RAG-поток не завершён end-to-end.
  Upload обновляет summary курса, но не индексирует контент: `app/services/upload_service.py:64-85`.
  Семантический поиск реализован только как in-memory FAISS: `app/services/embedding_service.py:8-23`.
  Search router существует, но не подключён в `main.py`: `app/routes/search.py:9-11`, `main.py:57-70`.
  `embed_and_add()` в рабочий upload/generation flow не встроен.

## Основные риски пилота

- Непредсказуемый запуск в разных средах.
- Потеря данных и рассинхрон схемы БД.
- Нерабочие или частично рабочие публичные endpoints.
- Утечка секретов и инфраструктурных параметров.
- Невозможность поддерживать пилот под нагрузкой из-за in-memory хранения и отсутствия очередей/observability.

## Проверки, выполненные по факту

- `venv\\Scripts\\python -c "import main"` без офлайн-переменных зависал на сетевой инициализации `SentenceTransformer` из `app/services/embedding_service.py:6`.
- `HF_HUB_OFFLINE=1` и `TRANSFORMERS_OFFLINE=1` позволяют импортировать приложение.
- `venv\\Scripts\\python -m pytest -q` в офлайн-режиме: **13 passed, 1 failed**.
  Падает `tests/test_external_sources.py::test_aggregated_search_empty` из-за отсутствующего `openalex_search`.
- `venv\\Scripts\\python -m alembic upgrade head`: падает на уже существующей таблице `courses`.

## Оценка объёма работ

- До минимального pilot-ready состояния текущего объёма: **примерно 2-3 недели одного сильного backend engineer** плюс **2-4 дня QA/DevOps**.
- Если брать расширенный backlog целиком, включая роли, очередь задач, storage abstraction, observability, compliance и SCORM/xAPI: это уже **отдельная программа на 6-10+ недель**.

## Что критично сделать первым

- Убрать `create_all()` из старта приложения и привести миграции в рабочее состояние.
- Привести env-профили, Docker Compose и stage/prod к одному рабочему способу конфигурации.
- Исправить сломанные публичные endpoints и покрыть их тестами.
- Перевести JWT/секреты на env без хардкода и защитить write-endpoints.
- Довести RAG до реально исполнимой цепочки `upload -> index -> search`.
