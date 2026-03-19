# Pilot Gap Analysis

## Функциональные разрывы

| Проблема | Последствия | Уровень риска | Рекомендация |
|----------|-------------|---------------|--------------|
| CRUD для теории и задач формально есть, но публичные ответы не соответствуют схемам. `TheoryResponse` и `TaskResponse` требуют `is_deleted`, а роуты его не возвращают: `app/schemas/theory.py:10-13`, `app/routes/theories.py:14,23,31`, `app/schemas/task.py:11-16`, `app/routes/tasks.py:14,22,31,39`. Ручная проверка 2026-03-19 дала 404/500. | Пользовательские сценарии управления контентом ломаются на рабочем API. | Высокий | Выровнять Request/Response-модели и добавить API-тесты на theory/task CRUD. |
| `course-structure` не проходит даже базовую валидацию: validator вызывает `.strip()` у списка `content_types`: `app/schemas/course_structure.py:13-17`, `app/schemas/course_structure.py:28-32`. | Невозможно создать структуру курса, а значит невозможна штатная генерация модулей по заявленному потоку. | Высокий | Исправить схемы `CourseStructureCreate/Update`, затем добавить API-тесты. |
| Версионирование не реализовано end-to-end. Роуты используют `ModuleVersion.module_id` и `LessonVersion.lesson_id`, которых в моделях нет: `app/routes/versioning.py:22-38`, `app/models/module_version.py:10-15`, `app/models/lesson_version.py:10-15`. | Версионные endpoint’ы падают 500; rollback отсутствует. | Критический | Либо быстро реализовать рабочие snapshot-модели и запись ревизий, либо убрать эти endpoint’ы и claims из README до пилота. |
| RAG upload обновляет только summary курса и не индексирует документ: `app/services/upload_service.py:64-85`. `embed_and_add()` в рабочие потоки не встроен: `app/services/embedding_service.py:13-18`. | Цепочка `upload -> search -> generate with context` фактически отсутствует. | Критический | Для пилота сделать минимальный pipeline: chunking, индексирование, метаданные, поиск по course/lesson. |
| `search` router существует, но не подключён в приложении: `app/routes/search.py:9-11`, `main.py:57-70`. | Semantic Search из README недоступен через API. | Критический | Подключить router, ограничить поиск по курсу и покрыть тестами. |
| Генерация контента урока удаляет задачи и тесты целого модуля, а не конкретного урока: `app/services/upload_service.py:34-39`. | Риск потери ранее сгенерированного контента в соседних уроках того же модуля. | Высокий | Привязать тесты/задачи к lesson-level или менять логику удаления на адресную. |
| Генерация модулей сохраняет только модули и уроки, а tests/tasks/theory оставлены комментарием `... и далее по аналогии`: `app/routes/generation.py:40-46`. | README обещает больше, чем реально записывается. | Средний | Явно ограничить scope пилота или дописать сохранение связанных сущностей. |

## Технические разрывы

| Проблема | Последствия | Уровень риска | Рекомендация |
|----------|-------------|---------------|--------------|
| На старте приложения выполняется `Base.metadata.create_all(bind=engine)`: `main.py:24`. Одновременно используется Alembic: `migrations/`. | Схема создаётся в обход миграций; `alembic upgrade head` конфликтует с уже существующими таблицами. | Критический | Убрать `create_all()` из production/startup path и сделать Alembic единственным источником схемы. |
| Цепочка миграций повреждена. Есть миграции с `pass`: `migrations/versions/3cdc28e805a5_add_theory_table.py:18-25`, `migrations/versions/fb808c2c30c8_add_owner_id_and_current_version_id_to_.py:18-25`, `migrations/versions/1b4264d70bcb_add_owner_id_and_current_version_id_to_.py:18-25`. Есть миграция с полностью закомментированным `upgrade()`: `migrations/versions/26cab4ad4380_test_modules.py:21-50`. | Невозможно доверять автогенерации, порядку и воспроизводимости схемы. | Критический | Пересобрать migration history, проверить `head`, `down_revision` и прогон на чистой БД. |
| Stage/prod env используют `postgresql+asyncpg`, а код базы синхронный и пакет `asyncpg` отсутствует: `.env.stage:4`, `.env.prod:4`, `app/database/db.py:9`, runtime check 2026-03-19. | Stage/prod не поднимаются в текущем виде. | Критический | Для пилота выбрать один стек: либо `psycopg2` + sync SQLAlchemy, либо полностью переходить на async. |
| `docker-compose` поднимает Postgres, но web остаётся на SQLite через `.env.dev`: `docker-compose.yml:21-22`, `.env.dev:4-5`. | Локальная/staging среда ведёт себя не так, как заявлено в README. | Критический | Переключить compose на Postgres DSN и добавить явный migration step. |
| В `requirements.txt` отсутствуют пакеты, которые требуются коду: `langchain_core`, `huggingface_hub`, `python-jose`, `passlib`, `email-validator`, `networkx`, `pytest`, `asyncpg`. См. импорты: `app/chat_engine/lc_engine.py:10-17`, `app/services/security.py:4-5`, `app/services/hf_infer_service.py:7`, `app/schemas/user.py:3`, `app/services/graph_service.py:3`. | Чистый build/container не воспроизводим. | Высокий | Привести зависимости к lock-file или полному `requirements.txt`. |
| Импорт `app/services/embedding_service.py` сразу создаёт `SentenceTransformer("all-MiniLM-L6-v2")`: `app/services/embedding_service.py:6`. Без офлайн-кэша импорт приложения зависает на сети. | Нестабильный старт приложения и тестов. | Высокий | Отложенная инициализация модели, локальный cache path, graceful degraded mode. |

## Архитектурные проблемы

| Проблема | Последствия | Уровень риска | Рекомендация |
|----------|-------------|---------------|--------------|
| Формально слои `routes / schemas / repositories / services / core` есть, но границы размыты. Прямые ORM-запросы и бизнес-логика встречаются в роутерах: `app/routes/versioning.py:12-38`, `app/routes/agent.py:16-26`, `app/routes/generation.py:38-47`. | Сложно тестировать, сложно безопасно менять контракты. | Средний | Для P0/P1 вынести use-case логику из route-слоя, особенно generation/versioning/upload. |
| Чат хранится in-memory в `app/routes/chat_storage.py:4-66`, а репозиторий чата лишь оборачивает route-level storage: `app/repositories/chat.py:1-28`. | История чатов теряется при рестарте, нет мультиинстанс-совместимости. | Высокий | Перенести chat storage в DB/Redis слой. |
| В проекте есть не подключённые роуты и мёртвый код: `app/routes/search.py`, `app/routes/graph.py`, `app/routes/feedback.py`, `app/routes/agent.py` не смонтированы в `main.py:57-70`. | README и реальное API расходятся; растёт технический долг. | Средний | Удалить/спрятать мёртвое, подключить только то, что реально поддерживается. |
| `app/routes/agent.py:7` импортирует `app.services.agents.course_agent`, но фактический файл лежит в `app/agents/course_agent.py`. Сам `course_agent` использует несуществующий `get_db_session`: `app/agents/course_agent.py:5,17`. | Код агента в текущем виде нерабочий даже при подключении. | Средний | Либо исправить модуль и зависимости, либо исключить из scope пилота. |

## Безопасность

| Проблема | Последствия | Уровень риска | Рекомендация |
|----------|-------------|---------------|--------------|
| JWT подписывается хардкодом `CHANGE_ME`: `app/services/security.py:7-26`. `JWT_SECRET` из env объявлен, но фактически не используется. | Любой пилотный контур получает компрометируемую auth-схему. | Критический | Перевести security-layer на `settings.JWT_SECRET` и `settings.JWT_ALG`, добавить тесты на auth flow. |
| В репозитории лежат секреты и тестовые токены: `.env.dev:7,24`, `.env.stage:6,14`, `.env.prod:6,14`. | Риск утечки и неправильного использования cred’ов. | Критический | Удалить секреты из git, оставить только `.env.example`, ротировать уже использованные секреты. |
| Большинство write-endpoints не защищены авторизацией: `courses`, `modules`, `lessons`, `tasks`, `tests`, `theories`, `upload`, `generation`, `course_structure` не используют auth dependency. | Любой клиент может менять данные и тратить LLM-ресурсы. | Критический | Для пилота закрыть write API JWT-авторизацией, read API ограничить по роли/owner. |
| `log.py:3` печатает `DATABASE_URL` при импорте. `app/services/generation_service.py:188,217` пишет полный prompt и raw output в лог. | Возможна утечка учётных данных и PII/контента в логах. | Высокий | Убрать print URL, ввести redaction и policy для LLM-логов. |
| Feedback endpoint не связывает автора с текущим пользователем. В схеме нет `author_id`, но модель требует его: `app/schemas/feedback.py:3-7`, `app/models/feedback.py:11-17`, `app/repositories/feedback.py:7-13`. | Endpoint не готов к безопасной эксплуатации; авторство и доступы не определены. | Средний | Ввести authenticated author binding через JWT и отдельную response schema. |

## Наблюдаемость и поддержка

| Проблема | Последствия | Уровень риска | Рекомендация |
|----------|-------------|---------------|--------------|
| `GET /api/healthz` не проверяет БД и возвращает только `{"ok": true}`: `app/routes/healthz.py:9-11`. | Нельзя использовать endpoint как реальный health gate для пилота. | Высокий | Разделить `healthz` и `readiness`, но сделать контракты явными и документированными; добавить проверки DB/index/storage/queue. |
| Метрик и трейсинга нет. Repo-wide search по `prometheus|opentelemetry|metrics|instrument` пустой. | Нет операционной наблюдаемости в пилотном контуре. | Средний | Добавить хотя бы Prometheus metrics и базовый request/LLM latency. |
| Нет graceful degraded mode для отсутствия внешних AI-зависимостей. `SentenceTransformer` грузится на импорте, HF/Giga discovery завязаны на сеть: `app/services/embedding_service.py:6`, `app/services/hf_infer_service.py:37-120`, `app/services/gigachat_service.py:70-135`. | Старт и health-check зависят от сети и внешних сервисов. | Высокий | Отложенная инициализация, таймауты, circuit-breaker/retry policy и fallback mode. |

## Документация

| Проблема | Последствия | Уровень риска | Рекомендация |
|----------|-------------|---------------|--------------|
| README описывает `.env`, `HF_EMAIL/HF_PASS` и `JWT_SECRET`, но код читает `.env.dev/.env.stage/.env.prod`, а Hugging Face использует `HF_TOKEN`: `README.md:49-63`, `app/core/config.py:4,30-32`, `app/services/hf_infer_service.py:99-120`. | Пользователь следует неверной инструкции и не может корректно запустить проект. | Высокий | Переписать раздел env и явно описать profile selection. |
| README утверждает, что `docker-compose` уже содержит healthcheck Postgres и автоподключение API: `README.md:88`. В `docker-compose.yml:1-27` этого нет. | Документация обещает поведение, которого нет. | Высокий | Исправить README или добавить реальный healthcheck и waiting strategy. |
| README обещает `/api/healthz` с `db: true`: `README.md:102-109`. Реальный код другой: `app/routes/healthz.py:9-17`. | Расхождение контракта и мониторинга. | Средний | Синхронизировать README и endpoint. |
| README заявляет автоматические Alembic миграции и staging TLS: `README.md:171-174`. В репозитории нет ни migration runner в Docker/CI, ни TLS-конфига. | Завышенные ожидания у release/stakeholders. | Высокий | Убрать claims до реализации. |

## Деплой и окружение

| Проблема | Последствия | Уровень риска | Рекомендация |
|----------|-------------|---------------|--------------|
| Dockerfile ставит только `requirements.txt`: `Dockerfile:7-17`. При этом `requirements.txt` неполный. | Чистый container build/running не гарантирован. | Высокий | Синхронизировать зависимости, собрать image на CI и прогонять smoke/migrations внутри образа. |
| Compose не запускает миграции перед web и не ждёт готовности БД: `docker-compose.yml:14-24`. | Гонки старта, нестабильное поведение при первом запуске. | Высокий | Добавить migration job и `depends_on` с `condition: service_healthy` после появления healthcheck. |
| В `.env.dev` реальные токены смешаны с локальными настройками, а stage/prod env-файлы лежат в repo. | Невозможно безопасно использовать репозиторий как deployment artifact. | Высокий | Оставить в git только `.env.example`, runtime secrets держать вне repo. |

## Тесты

| Проблема | Последствия | Уровень риска | Рекомендация |
|----------|-------------|---------------|--------------|
| В проекте только 14 тестов: `tests/` и repo-wide search по `def test_`. | Покрытие слишком узкое для пилотного релиза. | Высокий | Закрыть theory/task/test/course_structure/versioning/upload/auth. |
| Полный `pytest -q` не проходит: падает `tests/test_external_sources.py::test_aggregated_search_empty`. | Нельзя считать тестовый контур зелёным. | Высокий | Починить `external_sources` или тест, затем вернуть suite в зелёное состояние. |
| Нет интеграционного сценария `upload -> index -> search -> generate`. | Главный пилотный use-case не верифицирован. | Критический | Добавить один сквозной integration test на тестовой БД и локальном индексе. |
| Тесты проходят только в офлайн-режиме Hugging Face или при наличии локального кэша модели. | Clean CI/environment будет нестабилен. | Высокий | Зафиксировать offline/mocked mode для тестов и lazy init embedding-model. |
