# Notification center

Migration `mobile_0044` adds a private inbox and extends the existing push preferences. Inbox insertion is in the same transaction as the domain mutation and works with Expo disabled. Unique `(user_id, event_key)` prevents duplicate events. Existing notifications are not fabricated or backfilled from mutable assignment status.

Events: new assignment, confirmed grade revision, approaching due date, answer requiring a scoped reviewer, course generation completed/failed/paused. A notification links to a resource ID, never a caller-supplied URL.

Authenticated endpoints under `/api/notifications`: paginated list (`before_id`, `limit`, `category`, `unread`), detail, `/{id}/read`, `/read-all` with `through_id`, and GET/PUT `/preferences`. Read state is shared across web/native devices. Every inbox query is constrained by the current principal and organization. Target APIs still reauthorize resource access.

Push uses the encrypted durable outbox. A parent event fans out to one job per registered device, so partial provider failure cannot replay successful deliveries to other devices. Delivery rechecks account, company, session, auth version, installation token, preferences, read state and assignment/review applicability. Quiet-hour deferrals do not consume the retry budget. Expo receipts remove invalid tokens and delivery retries remain bounded. Delivery is at-least-once: an ambiguous network failure at the provider can still cause a duplicate notification; no exactly-once claim is made.

Lock-screen text contains only Lernium and a generic event description, with the inbox ID as data. No answers, names, email addresses or company titles are placed in push payloads. The client fetches the authorized inbox entry before navigating. Inbox entries remain available when push is disabled or denied.

Legacy `/auth/mobile/push-preferences` remains wire-compatible and preserves new preference fields. Legacy already-queued push payloads remain deliverable. New parent payloads carry `notification_id` and use per-device dispatch. Deadline preferences affect push; in-app history remains available.

Deployment prerequisites: migrations through `mobile_0044`; `DATA_ENCRYPTION_KEY`; `EXPO_PUSH_ENABLED`; Expo/FCM/APNs project credentials. Staging was upgraded on 2026-10-30 (Moscow) from PostgreSQL 17 Alpine to pgvector 0.8.7 / PostgreSQL 17.11 using a logical copy into a new named volume. All 87 existing table fingerprints matched before writers resumed; the original volume was retained. `POSTGRES_VOLUME` preserves the active volume across subsequent releases. Never mount the original Alpine data directory into the Debian-based image.

Migrations through `mobile_0044` passed both an isolated rehearsal against the existing encrypted backup and the staging deployment. A fresh post-migration backup was uploaded offsite, downloaded, checksum-verified and restored into an isolated pgvector container: 25 users, 29 courses and 21 uploaded files were preserved. The restore checker now uses the matching pgvector image (NeuroFlexDev/lernium-infra#4). The existing encryption key was preserved and Expo delivery enabled.

Staging acceptance exposed a PostgreSQL foreign-key failure during first mobile login: refresh history could be inserted before the new session. Session issuance now flushes the parent before adding token history, without committing independently. A regression exercises login, rotation and rollback with SQLite foreign keys enabled and with a disposable PostgreSQL schema in CI. It failed before the fix; the updated full CI suite passed 767 tests.

Expo and Android Firebase/FCM credentials are connected; native Android APK and iOS Simulator builds succeeded. FCM accepted a validation-only request. Physical-device delivery and encrypted persistence, iPhone signing/APNs, and store release remain pending. Neither mocked transport tests, provider validation nor native compilation proves real device delivery.
