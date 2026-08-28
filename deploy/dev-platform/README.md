# dev.platform.lernium.ru deployment

Production-like Docker Compose profile for the Lernium development platform.
It runs Caddy, the React frontend, FastAPI, an RQ worker, PostgreSQL and Redis.

## Expected checkout layout

```text
/opt/lernium/
├── ciBack/
└── ciFront/
```

The compose file lives in `ciBack/deploy/dev-platform` and uses the sibling
frontend checkout as its build context.

## Prerequisites

- Ubuntu server with Docker Engine and Docker Compose v2
- DNS `A` record for `dev.platform.lernium.ru` pointing at the server
- inbound TCP 80 and 443 allowed by the provider firewall
- at least 4 GB RAM, or permission to create the included 4 GB swap file

## First deployment

```bash
cd /opt/lernium/ciBack/deploy/dev-platform
chmod +x bootstrap.sh
sudo ./bootstrap.sh
```

The script creates `.env` with random PostgreSQL and JWT secrets when it is
missing. Add the required AI provider and SMTP credentials directly on the
server, then restart the application services:

```bash
docker compose up -d --force-recreate backend worker
```

Do not commit `.env`. The tracked `.env.example` contains placeholders only.

## Updating

Pull both repositories and rebuild from the deployment directory:

```bash
git -C /opt/lernium/ciBack pull --ff-only
git -C /opt/lernium/ciFront pull --ff-only
cd /opt/lernium/ciBack/deploy/dev-platform
COMPOSE_PROGRESS=plain docker compose build --pull
docker compose up -d --remove-orphans
```

Check the services and application health:

```bash
docker compose ps -a
curl --fail https://dev.platform.lernium.ru/api/healthz
```
