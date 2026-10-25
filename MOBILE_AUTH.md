# Native client authentication contract

The native client uses the tenant's own HTTPS API origin for every request. Do not accept an API origin, role, organization ID, or post-login redirect supplied by a user as authority. Show `display_domain` from bootstrap before starting sign-in. Invitation links should resolve to the same tenant origin.

## Bootstrap

`GET /api/auth/mobile/bootstrap` returns `api_origin`, `display_domain`, `login_methods`, `oidc_available`, `api_compatibility`, `minimum_app_version`, and `push_notifications`. App Links / Universal Links and the system browser must be configured by the mobile release; this backend does not configure native projects.

## Password, MFA, and sessions

`POST /api/auth/mobile/login` accepts `{ "email": "...", "password": "...", "device_name": "..." }`. Success returns a bearer access token, opaque refresh token, `expires_in`, and server `user`. If MFA is enabled it returns `{ "mfa_required": true, "challenge_token": "...", "expires_at": "..." }`; submit the challenge and OTP/recovery code to the existing `POST /api/auth/mfa/verify`. The resulting response is a mobile session.

`POST /api/auth/mobile/refresh` accepts `{ "refresh_token": "..." }`. It rotates the refresh token; discard the old token immediately. Refresh tokens are stored as hashes, expire according to `REFRESH_TOKEN_TTL_MINUTES`, and are invalid after rotation, revocation, account version change, or expiry. Reuse of an unknown/old token is rejected. Keep tokens in OS-protected secure storage.

`POST /api/auth/mobile/logout` requires the mobile bearer token and revokes that device. `POST /api/auth/mobile/logout-all` revokes every mobile refresh session for the authenticated user. Existing web `/api/auth/login`, `/api/auth/mfa/verify`, and `/api/auth/me` response contracts remain unchanged.

## OIDC browser handoff

Configure `MOBILE_OIDC_REDIRECT_URI` to a fixed HTTPS Universal Link / Android App Link, and publish the matching platform association files. Start the configured provider's `/api/auth/oidc/{provider_id}/start?mobile=true` URL in the system browser. The backend retains state, nonce, PKCE verifier, and browser binding; callback returns a short-lived one-use `sso_code` and `sso_state` to the fixed app link. Exchange them once at `POST /api/auth/oidc/exchange`; mobile handoff exchanges return the same mobile session contract, with MFA challenge when required. Cancellation returns `sso_error` to that fixed link. Never use a caller-provided callback URL.

## Customer hosted contour

Provide stable public DNS and HTTPS/TLS for the app and API, permit the app to reach API and readiness endpoints, and publish App Link / Universal Link association files. Deploy schema migrations before the compatible application release, retain the prior image and database backup for rollback, and honor `api_compatibility` and `minimum_app_version` when releasing clients. Push is optional: APNs/FCM credentials and outbound access must be configured by the customer; the app must continue to use the API without push. OIDC is tested first with an isolated Keycloak, then with the agreed pilot IdP; support is not implied for arbitrary providers.
