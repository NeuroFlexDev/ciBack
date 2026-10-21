# dev.platform.lernium.ru deployment

Docker Compose profile for the Lernium staging platform. Application images
are built by GitHub Actions and published to GHCR; the server never builds from
a working tree.

## Services

- `ciback` powers `backend`, the generation `worker`, durable delivery `outbox`, and one-shot `migrate` containers.
- `cifront` serves the React application on `127.0.0.1:8080`.
- `cilanding` serves the canonical landing page on `127.0.0.1:8081`.
- Caddy terminates TLS and routes landing, application, and API requests.
- PostgreSQL, Redis, uploads, and Caddy state live in named Docker volumes.

## Server bootstrap

The bootstrap script installs the runtime profile to `/opt/lernium/deploy`, so
deployments do not depend on a mutable Git checkout. Authenticate Docker to
GHCR, then run it from a checkout:

```bash
cd /opt/lernium/ciBack/deploy/dev-platform
chmod +x bootstrap.sh lernium-deploy
sudo ./bootstrap.sh
```

`bootstrap.sh` creates `.env` and `.images.env` when they do not exist. Add AI
provider and SMTP credentials to `.env`; never commit either environment file.
Set `PUBLIC_HOST` to the canonical hostname served by this profile (the default
is `dev.platform.lernium.ru`; production can use `lernium.ru`).

Install a dedicated, unprivileged SSH user for GitHub Actions. Grant that user
only this command via sudo:

```text
deploy ALL=(root) NOPASSWD: /usr/local/sbin/lernium-deploy *
```

The deploy script validates component names and immutable GHCR image references,
serializes concurrent deployments with `flock`, snapshots PostgreSQL before a
backend migration, runs migrations, checks both the local container and the
public health URL, and restores the previous image when a service does not
become healthy. Database migrations must remain backward-compatible with the
previous application image. After changing this script, rerun `bootstrap.sh`
on each target server so the installed sudo command is updated.

The helper discovers every long-running service using the backend image and requires
a healthcheck for each one. Backend rollout pulls and recreates all these services
together, waits for their health, and checks `/api/readiness` locally and publicly.
The RQ health probe verifies a live process and heartbeat in its own container;
an unrelated worker cannot make a failed pool healthy. Migrations are not reversed
automatically and the pre-migration dump remains available to the operator.

When first installing a changed Compose profile, preserve the previous
`docker-compose.yml`, `frontend-nginx.conf` and `Caddyfile` in the root-owned mode-0700
directory `/opt/lernium/deploy/.rollback-profile`, then create its `.pending` marker
before replacing the live files. Do not overwrite a previous snapshot without
archiving it. If backend deployment fails, the helper stops introduced services
(such as outbox on an older image), restores these previous files, recreates only
the previous backend service set and verifies readiness. Successful deployment
renames the pending marker and retains the snapshot. A failed rollback returns an
explicit error requiring operator recovery; it never reports success based only
on API liveness. Deploy profile/config changes only together with a compatible image.

After a successful compatible rollout, install the infra repository's
`lernium-maintenance` and `lernium-monitor` plus their timers. Maintenance runs legal
retention, expired delivery-secret removal, AI-history retention and learning-reminder
enqueueing. Outbox must be healthy before enabling mail delivery. Monitoring requires
an actual configured alert destination; timer installation alone is not alert acceptance.

## GitHub environment

Each repository uses a `staging` environment with:

- variables `STAGING_HOST`, `STAGING_USER`, `STAGING_BASTION_HOST`, and
  `STAGING_BASTION_USER`;
- secrets `STAGING_SSH_KEY` and `STAGING_KNOWN_HOSTS`.

The workflow passes its short-lived `GITHUB_TOKEN` to the deploy command through
stdin. The SSH bastion key is restricted to forwarding only the staging SSH
endpoint. No long-lived registry token is stored on either server.

## Manual checks

```bash
docker compose --env-file .env --env-file .images.env ps -a
curl --fail https://dev.platform.lernium.ru/api/healthz
curl --fail https://dev.platform.lernium.ru/
```

## Bounded load acceptance

`python -m scripts.seed_load_fixture seed --confirm-synthetic --manifest /protected/load.json`
creates a labelled synthetic organization, 300 distinct learners, 80 published courses
with 10 lessons each, and 3,000 assignments. It never changes customer accounts and
refuses production hostnames and production ENV. Because the shared deployment profile
uses production security defaults even on staging, invoke the seed process only with
`docker compose exec -T -e ENV=stage backend python -m scripts.seed_load_fixture ...`
on **dev.platform.lernium.ru**; this overrides ENV for that command only. The URL guard
still rejects production. No password is recorded, no email is sent, paid generation
quota is zero, and assistant/grading/images/integrations are disabled on this fixture.

Copy the mode-0600 bundle to a protected load-generator directory outside Git. Never
print it: it contains actual short-lived JWTs. Refresh with the `refresh` command and
`--output /protected/refreshed.json` if the seed/copy took too long. A normal 15-minute
token must have enough remaining lifetime for all four phases; the runner verifies this.
Run the client from a different machine so generator CPU does not contaminate server data:

```bash
python -m scripts.load_platform --bundle /protected/load.json --report /reports/load.json
```

The fixed phases are 25 users (90s hold, 15s ramp), 100 (120s/30s), 300 (180s/60s),
then a 300-user spike (45s, no ramp), with five-second jittered think time (roughly
18,000 requests across all phases, below the 20,000-request ceiling). Each learner
has a different JWT and a different assignment for each phase. Real routes check identity,
cross-organization denial, list/detail/results, answer drafts, immutable submissions
and optimistic progress revisions. No successful responses are synthesized. The test
stops on expired sessions, invalid fixture state, over 5% transport/server failures in
the last 100 requests, or 20,000 requests. 429s and all endpoint status counts remain
visible. It reports p50/p95/p99, bytes, requests per second and actual durations, without
claiming capacity from client concurrency alone. `--quick` is a separate two-user
smoke; use a separate fresh fixture because its first assignments are intentionally changed.

In parallel, run the infra `ops/lernium-sample-load --seconds 660 --interval 5` and save
its JSONL. It samples host CPU/load, RAM/swap/disk, per-container CPU/memory/PIDs,
image versions, Redis queue depth, DB connection counts and GPU availability without
collecting secrets or content. The one-sample read-only probe on the current staging
host passed. Combine both reports before changing the resource calculator or declaring
a supported user profile. Candidate-image load acceptance remains a release step.

After the run, `seed_load_fixture deactivate --confirm-synthetic --manifest ...`
revokes only that fixture's accounts/tokens and suspends its organizations, retaining
assignments/answers and an audit receipt. Do not delete test evidence or customer data.

This baseline has **zero paid AI calls**. It does not satisfy the OCR/chat/generation
part of R13-05 by itself. That concurrent side workload requires an approved provider,
real prices, isolated organization quotas and a hard monetary reservation covering
all stages, embeddings/OCR and retries. Missing prices/reservation mean no paid run.
Local-only AI requires actual installed local models and blocked-external-network
acceptance; a mocked provider does not qualify. Only measured generation durations,
worker counts and memory may enter the R13-06 delivery profile/calculator.
