# Notification center

Migration `mobile_0044` adds a private inbox and extends the existing push preferences. Inbox insertion is in the same transaction as the domain mutation and works with Expo disabled. Unique `(user_id, event_key)` prevents duplicate events. Existing notifications are not fabricated or backfilled from mutable assignment status.

Events: new assignment, confirmed grade revision, approaching due date, answer requiring a scoped reviewer, course generation completed/failed/paused. A notification links to a resource ID, never a caller-supplied URL.

Authenticated endpoints under `/api/notifications`: paginated list (`before_id`, `limit`, `category`, `unread`), detail, `/{id}/read`, `/read-all` with `through_id`, and GET/PUT `/preferences`. Read state is shared across web/native devices. Every inbox query is constrained by the current principal and organization. Target APIs still reauthorize resource access.

Push uses the encrypted durable outbox. A parent event fans out to one job per registered device, so partial provider failure cannot replay successful deliveries to other devices. Delivery rechecks account, company, session, auth version, installation token, preferences, read state and assignment/review applicability. Quiet-hour deferrals do not consume the retry budget. Expo receipts remove invalid tokens and delivery retries remain bounded. Delivery is at-least-once: an ambiguous network failure at the provider can still cause a duplicate notification; no exactly-once claim is made.

Lock-screen text contains only Lernium and a generic event description, with the inbox ID as data. No answers, names, email addresses or company titles are placed in push payloads. The client fetches the authorized inbox entry before navigating. Inbox entries remain available when push is disabled or denied.

Legacy `/auth/mobile/push-preferences` remains wire-compatible and preserves new preference fields. Legacy already-queued push payloads remain deliverable. New parent payloads carry `notification_id` and use per-device dispatch. Deadline preferences affect push; in-app history remains available.

Deployment prerequisites: migrations through `mobile_0044`; `DATA_ENCRYPTION_KEY`; `EXPO_PUSH_ENABLED`; Expo/FCM/APNs project credentials. The current staging database was observed running PostgreSQL 17 Alpine without the pgvector image required by earlier migration `retrieval_0037`. Rehearse an extension/image migration with a verified backup before deploying this branch. Do not simply mount the existing data volume into a different Linux distribution's PostgreSQL image.

Physical-device delivery and production release are pending external account setup; passing mocked transport tests is not evidence of real Expo/APNs/FCM delivery.
