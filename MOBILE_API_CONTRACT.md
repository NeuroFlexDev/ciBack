# Контракт API мобильного пилота

Этот документ фиксирует backend-контракт для первого iOS/Android клиента. Базовый URL — `api_origin` из публичного bootstrap. Все JSON запросы и ответы используют UTF-8 и `Content-Type: application/json`, кроме скачивания опубликованных файлов. Все методы ниже требуют `Authorization: Bearer <access_token>`, кроме bootstrap, входа/обновления сессии, MFA и OIDC handoff, явно отмеченных в таблице. Backend является источником истины для пользователя, организации, ролей, прав и доступности контента.

## Совместимость клиента

Сразу после bootstrap клиент сравнивает `api_compatibility` с поддерживаемой им строкой **на точное совпадение**, а свою версию — с `minimum_app_version`. При несовпадении compatibility tag или версии ниже минимума приложение не запрашивает и не показывает пользовательские данные и предлагает обновить приложение. Увеличение `minimum_app_version` должно сопровождать повышение минимальной версии, требуемой сервером. Compatibility tag сохраняется для обратно совместимых расширений; несовместимое изменение требует нового tag и одновременно заданного минимума приложения. Значения задаются развертыванием backend.

Cookies, WebView storage и browser local/session storage не являются хранилищем сессии приложения. Access и refresh token сохраняются в системном защищённом хранилище. На refresh клиент атомарно заменяет оба токена и немедленно забывает старый refresh token. Повторное использование старого refresh token отзывает сессию. После 401 клиент может один раз обновить сессию; при неудаче очищает её и требует вход. Вызов MFA verify возвращает мобильную пару токенов после успешной проверки. OIDC запускается системным браузером через фиксированную Universal Link/App Link и завершается одноразовым обменом кода; callback URL, заданный клиентом, не принимается.

При выходе или смене аккаунта клиент отменяет активные запросы и очищает весь query cache. Ключи кэша включают идентификаторы пользователя и организации. UI не использует кэшированные данные после смены principal. Изменение принадлежности, полномочий и доступности объекта всегда проверяется сервером.

## Эндпоинты

| Операция | Метод и путь | Вход → ответ | Сессия и пагинация |
| --- | --- | --- | --- |
| Конфигурация и совместимость | `GET /api/auth/mobile/bootstrap` | Без тела → `MobileBootstrapResponse` | Публичный; без пагинации |
| Вход паролем | `POST /api/auth/mobile/login` | `{email,password,device_name?}` → `MobileSessionResponse` либо `MobileMFAChallengeResponse` | Публичный; `device_name` по умолчанию `Mobile device` |
| Проверка MFA | `POST /api/auth/mfa/verify` | `{challenge_token,code}` → мобильная сессия при mobile challenge | Публичный, challenge одноразовый и ограничен по времени |
| Начало OIDC | `GET /api/auth/oidc/{provider_id}/start?mobile=true` | Без тела → redirect на IdP | Публичный; используется системный браузер |
| Обмен OIDC handoff | `POST /api/auth/oidc/exchange` | `{code,state}` → мобильная сессия либо MFA challenge | Публичный; код одноразовый и краткоживущий |
| Ротация сессии | `POST /api/auth/mobile/refresh` | `{refresh_token}` → `MobileSessionResponse` | Публичный; предыдущий refresh token сразу недействителен |
| Выход с устройства | `POST /api/auth/mobile/logout` | Пустое тело → `{ok:true}` | Bearer; отзывает текущую мобильную сессию |
| Выход со всех устройств | `POST /api/auth/mobile/logout-all` | Пустое тело → `{ok:true}` | Bearer; отзывает мобильные refresh-сессии пользователя |
| Профиль | `GET /api/auth/me` | Без тела → `UserResponse` | Bearer |
| Изменить имя | `PATCH /api/me` | `{full_name}` → `UserResponse` | Bearer |
| Список назначений | `GET /api/learning/assignments?limit=50&offset=0&include_history=false` | Без тела → `{total,items: AssignmentSummaryResponse[]}` | Bearer; `limit` 1–100, `offset` ≥0; история по умолчанию скрыта |
| Назначение и опубликованный курс | `GET /api/learning/assignments/{assignment_id}` | Без тела → `AssignmentDetailResponse` (`content` — опубликованный снимок, `responses`) | Bearer |
| Опубликованное вложение | `GET /api/learning/assignments/{assignment_id}/sources/{document_id}/content` | Без тела → байты файла с серверным MIME type и Content-Disposition | Bearer; только файл опубликованной версии назначенного курса |
| Сохранить ответ | `PUT /api/learning/assignments/{assignment_id}/responses/{assessment_id}` | `AnswerInput` → `LearningResponseItem` | Bearer |
| Отправить ответ | `POST /api/learning/assignments/{assignment_id}/responses/{assessment_id}/submit` | `SubmitInput` → `LearningResponseItem` | Bearer; `submission_key` — ключ идемпотентности |
| Результаты | `GET /api/learning/assignments/{assignment_id}/results` | Без тела → `ResultsResponse` | Bearer |
| Обновить прогресс | `PUT /api/learning/assignments/{assignment_id}/progress` | `{expected_revision,lesson_id,completed?}` → `AssignmentSummaryResponse` | Bearer; конфликт ревизии — 409 |
| Закладка | `PUT /api/learning/assignments/{assignment_id}/bookmark` | `{expected_revision,item_key}` → `AssignmentSummaryResponse` | Bearer; конфликт ревизии — 409 |
| История ассистента | `GET /api/learning/assignments/{assignment_id}/assistant/messages?limit=20&offset=0` | Без тела → `AssistantHistoryResponse` | Bearer; `limit` 1–100, `offset` ≥0 |
| Вопрос ассистенту | `POST /api/learning/assignments/{assignment_id}/assistant/messages` | `{lesson_id,question,request_id}` → `AssistantMessageResponse` | Bearer; `request_id` — ключ идемпотентности |
| Очистить историю | `DELETE /api/learning/assignments/{assignment_id}/assistant/messages` | Без тела → `204 No Content` | Bearer |

`AnswerInput`: `{expected_revision?: integer (default 0), attempt?: integer (default 1), answer: {text?: string, selected?: string[]}}`. `SubmitInput` содержит те же поля и обязательный `submission_key` длиной 8–64 символа. `ProgressInput` требует `expected_revision`, ID урока и принимает `operation_key` (стабильный ключ повтора, уникальный в рамках назначения). `BookmarkInput` принимает тот же `operation_key` и `item_key` — опубликованный ключ урока (`lesson:<id>`) или ID опубликованного assessment. Повтор с прежним ключом и тем же payload возвращает текущее состояние назначения; другой payload с тем же ключом даёт `409`. Для операций без ключа сохраняется прежняя проверка ревизии. Конфликт ревизии `409` включает `current_revision` и состояние назначения; конфликт ответа также включает `current_response`. Назначение сохраняет `course_version_id` и `revision` опубликованной версии. Черновик ответа имеет `status=draft`; в результаты он попадает только после submit. Сервер схемы входа находятся в `app/schemas/learning.py`; response schemas для мобильного OpenAPI — в `app/schemas/mobile_contract.py`.

## Ответы и ошибки

Успешные ответы — JSON, кроме binary content и `204` при удалении истории. Auth/session поля определены серверными `UserResponse`, `MobileSessionResponse`, `MobileMFAChallengeResponse`; поля назначения и ответа определены `AssignmentSummaryResponse`, `AssignmentDetailResponse`, `LearningResponseItem` и `ResultsResponse`. Не показывайте `correct_answers`, если сервер не вернул их: доступность зависит от правил назначения и результата. У ассистента `can_send` и `disabled_reason` в истории описывают настройки организации/назначения; сервер всё равно повторно проверяет право при отправке.

| HTTP | Значение для клиента |
| --- | --- |
| 401 | Нет/истёк bearer либо сессия отозвана. Для защищённого запроса попробуйте единственную ротацию refresh; иначе завершите сессию и покажите вход. Неверный refresh не повторять бесконечно. |
| 403 | Доступ запрещён, функция выключена политикой или право отозвано. Обновите данные/права; не повторяйте автоматически. |
| 404 | Назначение, урок, assessment или вложение отсутствует либо недоступно в опубликованной версии. Покажите, что материал недоступен. |
| 409 | Конфликт ревизии/состояния. Обновите объект и предложите повторить действие; не перезаписывайте новую ревизию вслепую. |
| 429 | Ограничение частоты/квоты. Учитывайте `Retry-After`, если он передан, и предложите повтор позже. |
| 5xx | Временная серверная ошибка. Покажите возможность повтора; не повторяйте небезопасную операцию без её idempotency key. |
| Таймаут/отмена | Отмена пользователем или навигацией не считается подтверждением операции. Для отправки ответа/вопроса безопасно сверяйте состояние по ключу операции/истории перед повтором. |

Ошибки FastAPI обычно имеют JSON вида `{ "detail": ... }`; `detail` может быть строкой или объектом, например `{message,current_revision}` при конфликте прогресса. Общий middleware возвращает `X-Request-ID` и включает `request_id` в JSON-ошибку, когда они доступны. Передавайте ID в поддержку и показывайте его в диагностике; не отображайте пользователю сырой traceback или внутренние детали.

## Границы пилота и приёмка

Для вложений используйте только авторизованный endpoint назначения: публичные storage URL и URL из manifest не являются разрешением доступа. Backend проверяет принадлежность пользователя и организации при чтении каждого назначения, вложения, ответа, прогресса и сообщения ассистента. Пагинация назначений и истории ассистента — offset/limit; `total` сообщает полное число доступных записей.

В backend существуют методы push-регистрации и пользовательских настроек. `PUT /api/auth/mobile/push-device` принимает Expo `token` и `platform` (`ios` или `android`) под bearer-токеном мобильной сессии. При переносе Expo token новая сессия становится владельцем регистрации. `DELETE` удаляет регистрацию текущей сессии; mobile logout и logout-all также удаляют токены.

`GET/PUT /api/auth/mobile/push-preferences` возвращают/принимают `{assignment_enabled,deadline_enabled,result_enabled,timezone}`. Все переключатели по умолчанию включены; timezone — валидное IANA имя, по умолчанию `UTC`. Bootstrap сообщает `push_notifications:true` только когда Expo отправка включена и ключ шифрования токенов настроен.

Push payload безопасен для экрана блокировки: общие тексты, `data.type`, `data.assignment_id`, `data.route` (`/learning/assignments/{id}`). В payload не включаются название курса, ответы, оценки, email или имя. Открытие push не авторизует пользователя: клиент обязан выполнить новый API запрос с текущей bearer-сессией, а API повторно проверит владельца назначения.

В backend уже существуют прочие описанные методы; mock endpoints для мобильного пилота не предусмотрены. Интеграционная приёмка должна сверять OpenAPI с фактическими payload: MFA и OIDC handoff, ротацию/отзыв/истечение mobile session, пагинацию и request ID ошибок, отказ в чтении чужого назначения/вложения, конфликт прогресса, save/submit/result ответа и политику ассистента. Клиентские проверки secure storage, очистки кэша/отмены и блокировки несовместимых версий требуют RN-приложения и выполняются отдельно.
