#!/usr/bin/env bash
set -euo pipefail

deployment_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${deployment_dir}"
umask 077

if [[ ! -f .env ]]; then
    postgres_password="$(openssl rand -hex 32)"
    jwt_secret="$(openssl rand -hex 48)"

    printf '%s\n' \
        'POSTGRES_USER=ciuser' \
        "POSTGRES_PASSWORD=${postgres_password}" \
        'POSTGRES_DB=cidb' \
        "JWT_SECRET=${jwt_secret}" \
        'SMTP_HOST=' \
        'SMTP_PORT=587' \
        'SMTP_USER=' \
        'SMTP_PASS=' \
        'HF_TOKEN=' \
        'HF_MODEL=' \
        'HF_MODEL_CANDIDATES=' \
        'HF_API_URL=' \
        'GIGA_CLIENT_ID=' \
        'GIGA_CLIENT_SECRET=' \
        'GIGA_SCOPE=GIGACHAT_API_PERS' > .env
fi

chmod 600 .env

if [[ ! -e /swapfile ]]; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
fi

install -m 0644 swapfile.swap /etc/systemd/system/swapfile.swap
systemctl daemon-reload
systemctl enable --now swapfile.swap

docker compose config --quiet
COMPOSE_PROGRESS=plain docker compose build --pull
docker compose up -d --remove-orphans
