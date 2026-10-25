# Mobile deep-link contract

The native client remains in LER-89/LER-86/LER-25. The web application remains the fallback when the app is absent. Only these public origins and routes are approved:

| Contour | HTTPS origin | Routes |
| --- | --- | --- |
| DEV | `https://dev.platform.lernium.ru` | `/invite`, `/auth`, `/learning/assignments/{assignment_id}` |
| PROD | `https://lernium.ru` | `/invite`, `/auth`, `/learning/assignments/{assignment_id}` |

Invitation links are generated from the server's configured `PUBLIC_APP_URL` and use `/invite#token=…`. Email confirmation links use `/auth#email_token=…`. The fragment keeps bearer-like values out of HTTP request logs and referrer headers. The existing web pages continue to process these links.

## Native handoff

- Handle both cold-start links and links delivered while the app is open. Parse only the configured origin and the three route families above; reject unknown hosts, schemes, and paths. Never derive an API origin or redirect from link input.
- Keep invite/email tokens in memory or OS-protected storage while the user signs in or completes MFA. Clear them after completion, expiry, or explicit cancellation.
- For invitations, call `POST /api/auth/invitations/inspect` before presenting details. New accounts use `/api/auth/invitations/accept`; existing accounts sign in and use `/api/auth/invitations/accept-existing`. A token addressed to another email must prompt an account switch and must not be accepted with the current account.
- For email binding, retain `email_token` through sign-in and MFA, then call authenticated `POST /api/me/email/confirm`. The server enforces expiry, single use, account identity, and MFA.
- For assigned learning, retain the assignment ID through sign-in and MFA, then fetch `GET /api/learning/assignments/{assignment_id}`. The existing learner authorization check rejects assignments belonging to another user or organization.
- After each action, continue in the native app. In a browser without the app, keep the existing web fallback and login flow.

Invitation tokens expire after seven days and are single-use. Email-binding tokens expire after 30 minutes and are single-use. Server APIs remain authoritative; association files only authorize OS-level app routing.

## Publish and verify DEV/PROD

Obtain the exact DEV and PROD Apple Team ID, bundle ID, Android package name, and signing-certificate SHA-256 fingerprints from LER-89. Do not use placeholder or debug signing values in production. Generate each contour's files in that contour's own deployment checkout, then deploy the matching association directory with Caddy:

```powershell
$env:PUBLIC_HOST = 'dev.platform.lernium.ru'
$env:APPLE_TEAM_ID = '<DEV_TEAM_ID>'
$env:APPLE_BUNDLE_ID = '<DEV_BUNDLE_ID>'
$env:ANDROID_PACKAGE = '<DEV_PACKAGE>'
$env:ANDROID_SHA256_CERT_FINGERPRINTS = '<DEV_SHA256_FINGERPRINT>'
python ..\generate_associations.py --output .\associations
```

Repeat in the PROD checkout using `PUBLIC_HOST=lernium.ru` and PROD identifiers. Set the backend `PUBLIC_APP_URL` to that same contour's HTTPS origin. Do not copy association files between contours. The generator rejects any other public hostname and only emits the approved route paths.

For each deployed contour, verify externally:

```text
curl -i https://<contour>/.well-known/apple-app-site-association
curl -i https://<contour>/.well-known/assetlinks.json
```

Both responses must be direct `200` responses over valid public HTTPS, without redirects, cookies, or authentication, with JSON bodies matching that contour's app identifiers and fingerprints. Confirm DEV contains only DEV app IDs/certificates and PROD only PROD values. Validate the final files with Apple's and Google's platform validators and install the matching signed build before device acceptance. Verify that `/invite` and `/auth` still render the web app when no native app is installed.

The backend already tests invitation expiry/replay and existing-account protection (`tests/test_administration.py`), email token expiry/replay and authenticated confirmation (`tests/test_accounts_release.py`), and per-user assignment authorization (`tests/test_learning_runtime.py`). Device-level iOS/Android acceptance still requires the RN builds and real signing identifiers from LER-89.
