# Pilot Readiness Plan

## P0

| Заголовок | Описание | Файлы/модули | Оценка влияния на пилот | Критерии приемки |
|-----------|----------|--------------|--------------------------|------------------|
| Убрать конфликт `create_all()` и Alembic | Сделать Alembic единственным источником схемы, убрать `Base.metadata.create_all()` из startup, восстановить рабочий migration chain | `main.py`, `migrations/env.py`, `migrations/versions/*` | Без этого нельзя стабильно деплоить и обновлять схему | `alembic upgrade head` проходит на пустой БД; app стартует после миграций; в CI есть отдельный шаг миграций |
| Привести env-профили к одному рабочему способу | Исправить выбор профиля, убрать зависимость от случайного `ENV`, выровнять `.env.example`, `.env.dev`, `.env.stage`, `.env.prod`, удалить секреты из repo | `app/core/config.py`, `.env.example`, `.env.dev`, `.env.stage`, `.env.prod`, `docker-compose.yml`, `README.md` | Без этого stage/prod не поднимаются предсказуемо | Локально, в Compose и в stage используется один и тот же documented DSN pattern; секреты в repo отсутствуют |
| Починить сломанные публичные контракты | Выровнять Request/Response схемы и исправить theory/task/test/course_structure/versioning endpoints | `app/routes/theories.py`, `app/routes/tasks.py`, `app/routes/tests.py`, `app/routes/course_structure.py`, `app/routes/versioning.py`, `app/schemas/*`, `app/models/*` | Без этого core API пилота нерабочий | API-тесты на все перечисленные endpoints зелёные; ручная проверка больше не даёт 404/500 на валидных запросах |
| Исправить auth и секреты JWT | Перевести security на `settings.JWT_SECRET`, убрать hardcoded secret, согласовать path `/api/auth/*` или поправить `tokenUrl`, закрыть write-endpoints авторизацией | `app/services/security.py`, `app/services/auth.py`, `app/routes/auth.py`, `main.py`, write-routes | Без этого пилот небезопасен | Регистрация/логин/me работают; Swagger auth использует существующий endpoint; write API без токена получает 401/403 |
| Убрать сетевую инициализацию на импорт и стабилизировать embedding | Ленивая инициализация `SentenceTransformer`, локальный cache/fallback, mocked mode для тестов | `app/services/embedding_service.py`, тестовые фикстуры | Без этого app/test startup нестабилен | `import main` и `pytest -q` не требуют сети и не зависят от случайного кэша |
| Довести RAG до минимально рабочего pilot-flow | После upload индексировать chunks документа, сохранять метаданные и открыть рабочий search endpoint | `app/services/upload_service.py`, `app/services/embedding_service.py`, `app/routes/search.py`, `main.py` | Это центральный use-case пилота | Сценарий `upload -> search` работает на тестовом документе; есть integration test |
| Перевести Compose на реальную пилотную схему запуска | Web должен использовать тот же Postgres, который поднимает Compose; добавить migration step и healthcheck | `docker-compose.yml`, `.env.dev`, `Dockerfile` | Иначе staging-like запуск недостоверен | `docker compose up` поднимает БД и API без ручных правок; app реально пишет в Postgres |
| Расширить тестовый контур до pilot-minimum | Добавить API и integration tests для auth, theories, tasks, tests, course_structure, versioning, upload->search->generate | `tests/*` | Без этого высокий риск регрессий | Полный `pytest -q` зелёный; критические сценарии закрыты |

## P1

| Заголовок | Описание | Файлы/модули | Оценка влияния на пилот | Критерии приемки |
|-----------|----------|--------------|--------------------------|------------------|
| Нормализовать сервисный слой LLM | Выделить единый adapter layer для HF/Giga, добавить retries, timeout policy, latency logging и redaction | `app/services/generation_service.py`, `app/services/hf_infer_service.py`, `app/services/gigachat_service.py`, `app/services/llm_registry.py` | Сильно повышает надёжность и поддержку | Ошибки внешних LLM обрабатываются единообразно; есть таймауты и метрики latency |
| Централизовать логирование без утечки чувствительных данных | Убрать `print(DATABASE_URL)`, ввести redaction и policy для prompt/raw-output логов, сохранить request_id | `log.py`, `main.py`, LLM services | Важный operational hardening | Секреты и DB URL не появляются в логах; request_id проходит через все error/info логи |
| Упорядочить слои и убрать мёртвый код | Вынести прямую ORM/business logic из роутов, зачистить неиспользуемые или не подключённые маршруты и сервисы | `app/routes/*`, `app/services/*`, `app/repositories/*`, `app/agents/*` | Снижает стоимость изменений перед пилотом | Слой route не содержит тяжёлой доменной логики; неиспользуемый код либо удалён, либо скрыт от API |
| Добавить persistent chat и course-aware QA | Перевести чат из in-memory в БД/Redis, привязать к курсу и RAG-контексту | `app/routes/chat.py`, `app/routes/chat_storage.py`, `app/repositories/chat.py`, `app/services/chat_service.py` | Желательно, если QA-chat входит в pilot scope | История не теряется при рестарте; есть курс-специфичный контекст |
| Синхронизировать OpenAPI и README | Исправить теги, примеры, контракты и запуск по фактическому состоянию | `README.md`, route docstrings, schemas | Снижает ошибки интеграции | Swagger и README совпадают с реально работающими endpoint’ами |
| Ввести минимальные роли и owner-based access | Для пилота достаточно editor/admin или owner-based model без полного RBAC | `app/models/user.py`, новые role tables, protected routes | Повышает безопасность и управляемость пилота | Пользователь не может менять чужие курсы; роль учитывается в write-операциях |

## P2

| Заголовок | Описание | Файлы/модули | Оценка влияния на пилот | Критерии приемки |
|-----------|----------|--------------|--------------------------|------------------|
| Очередь фоновых задач | Вынести тяжёлые LLM/RAG операции в Celery/RQ/Arq | новый worker-layer, generation/upload services | Можно перенести после пилота при малых объёмах | Длинные операции выполняются асинхронно, есть retry/status |
| Storage abstraction | Слой локальный диск / S3 / MinIO для документов и экспортов | upload/storage services | После пилота, если не нужен внешний storage сразу | Upload/download не завязаны на локальный temp-only flow |
| Compliance/PII | Маскирование PII и флаги прохождения проверки | preprocessing services, audit logs | После пилота, если нет регуляторного требования прямо сейчас | Для документа есть status проверки и лог решения |
| Метрики и трейсинг | Prometheus/OpenTelemetry, модельные и queue metrics | observability layer | Желательно после стабилизации P0/P1 | Есть технические и продуктовые метрики, пригодные для эксплуатации |
| Rate limiting и anti-abuse для LLM | Ограничения на генерации и chat usage | API middleware, auth/user quota tables | Полезно после базовой auth-модели | Можно задать лимит на пользователя/курс/период |
| SCORM/xAPI экспорт | Версионный экспорт курсов и endpoint скачивания | export services, storage | После пилота | Есть файл экспорта, связанный с версией курса |
