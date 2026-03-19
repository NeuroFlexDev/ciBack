# Roadmap

## Milestone 0: Stabilize To Pilot

- Привести конфигурацию окружений к одному рабочему контракту.
- Убрать конфликт `create_all()` и Alembic.
- Починить theory/task/test/course-structure/versioning endpoints.
- Закрыть write-endpoints JWT-авторизацией.
- Довести до рабочего состояния минимальный RAG-flow `upload -> index -> search`.
- Расширить тесты до pilot-minimum и вернуть suite в зелёное состояние.

## Milestone 1: Harden Pilot

- Нормализовать LLM service layer, retries, timeouts и redaction.
- Перевести чат на persistent storage и добавить course-aware context.
- Ввести owner-based access или минимальные роли.
- Добавить реальные health/readiness сигналы и базовые метрики.
- Синхронизировать README и OpenAPI с фактическим API.

## Milestone 2: Post-Pilot Expansion

- Очередь фоновых задач.
- Storage abstraction для S3/MinIO.
- Rate limiting и anti-abuse для LLM.
- Compliance/PII processing.
- SCORM/xAPI экспорт.
- Полный observability stack.

## Reference

- Детальная разбивка по приоритетам: `PILOT_READINESS_PLAN.md`
- Детальный аудит и риски: `PILOT_GAP_ANALYSIS.md`
