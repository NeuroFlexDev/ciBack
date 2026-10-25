# Mobile push notifications

Backend sends push messages through the Expo Push Service from the durable `outbox` worker. The worker needs HTTPS egress to `exp.host` on port 443. It does not need inbound network access from Expo.

## Configuration

- Keep the existing `DATA_ENCRYPTION_KEY` configured and backed up. Mobile Expo tokens are encrypted with this key; losing it prevents delivery and existing tokens must be registered again.
- Set `EXPO_PUSH_ENABLED=true` to advertise push availability in `GET /api/auth/mobile/bootstrap` and enqueue new notifications.
- Set `EXPO_PUSH_ACCESS_TOKEN` to the Expo project access token when push security is enabled in Expo. Keep it in the private runtime environment; it is not returned by the API.
- Configure the Expo/EAS project ID, iOS bundle identifier, Android application ID, APNs credentials, and FCM credentials in the RN repository's EAS project. Build and sign the app for its actual identifiers before device acceptance.
- Deploy the `mobile_0043` migration before starting the new backend and outbox image. It removes old hash-only device registrations because they cannot be sent; clients register their current Expo token again.

The customer deployment shares `runtime.env` across backend, worker, outbox, and migration containers. The development Compose profile passes the Expo variables through from its private `.env` file. Leave `EXPO_PUSH_ENABLED=false` until EAS credentials and outbound HTTPS are ready.

## Delivery behavior

Assignment and confirmed grading events are inserted into `outbound_messages` in the same transaction as the learning change. The outbox worker sends only generic title/body text and `data.type`, `data.assignment_id`, and `data.route`. A result event is emitted for automatic grading and each reviewer-confirmed revision; drafts and pending manual reviews do not produce result messages.

Deadline reminders are scanned once per minute by the outbox process. They use the user's IANA timezone and the program's `reminder_days` (default 3), and become eligible at 09:00 on the local calendar date N days before the due date. Completed and revoked assignments are skipped. The event key includes assignment ID and local due date, so repeated scans do not enqueue duplicates.

Delivery rechecks the assignment, preference, user, active mobile session, session auth version, and encrypted device registration immediately before contacting Expo. Logout and logout-all delete registrations. Message TTL is two minutes and the outbox event expires after two days. Transient provider or network errors retry with bounded exponential delay. Expo `DeviceNotRegistered` tickets delete the matching token. Expo tickets are at-least-once: if the provider accepts a message but the worker loses the response, a retry may deliver a duplicate.

## Operations and diagnosis

Inspect `outbound_messages` for `kind='push'`, ordered by `created_at`. `pending` means queued or waiting for retry; `sending` with an expired `lease_until` will be reclaimed; `delivered`, `cancelled`, and `expired` are terminal; `failed` exhausted bounded retries. `last_error` intentionally stores exception class only, never provider response bodies or token values.

Check the outbox container health and logs, `EXPO_PUSH_ENABLED`, `DATA_ENCRYPTION_KEY`, Expo project credentials, DNS and HTTPS egress. For a specific notification, inspect its deduplication key (`push:<type>:...`) and status. Successful Expo tickets enqueue a receipt check for 15 minutes later; receipt `DeviceNotRegistered` removes the matching registration. Receipt polling uses the same bounded retry and expiry policy as other outbox work.

## API and privacy

Settings are account-wide while each token is bound to a mobile session. `GET/PUT /api/auth/mobile/push-preferences` controls assignment, deadline, and result messages and the IANA timezone. A disabled device permission does not prevent normal learning. Push payloads contain no course title, answer, grade, email, or user name. Notification opens are hints only: the app must authenticate and fetch the assignment from the API, which checks current ownership and access.
