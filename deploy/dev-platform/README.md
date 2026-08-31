# dev.platform.lernium.ru deployment

Docker Compose profile for the Lernium staging platform. Application images
are built by GitHub Actions and published to GHCR; the server never builds from
a working tree.

## Services

- `ciback` powers `backend`, `worker`, and one-shot `migrate` containers.
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

Install a dedicated, unprivileged SSH user for GitHub Actions. Grant that user
only this command via sudo:

```text
deploy ALL=(root) NOPASSWD: /usr/local/sbin/lernium-deploy *
```

The deploy script validates component names and immutable GHCR image references,
serializes concurrent deployments with `flock`, snapshots PostgreSQL before a
backend migration, runs migrations, checks service health, and restores the
previous image when a service does not become healthy. Database migrations must
remain backward-compatible with the previous application image.

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
