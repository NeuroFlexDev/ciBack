#!/usr/bin/env bash
set -euo pipefail

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
deployment_dir="/opt/lernium/deploy"
[[ ${EUID} -eq 0 ]] || { printf 'bootstrap.sh must run as root\n' >&2; exit 1; }

install -d -m 0755 "${deployment_dir}"
install -m 0644 "${source_dir}/docker-compose.yml" "${deployment_dir}/docker-compose.yml"
install -m 0644 "${source_dir}/Caddyfile" "${deployment_dir}/Caddyfile"
install -m 0644 "${source_dir}/frontend-nginx.conf" "${deployment_dir}/frontend-nginx.conf"
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

if [[ ! -f .images.env ]]; then
    printf '%s\n' \
        'BACKEND_IMAGE=ghcr.io/neuroflexdev/ciback:main' \
        'FRONTEND_IMAGE=ghcr.io/neuroflexdev/cifront:main' \
        'LANDING_IMAGE=ghcr.io/neuroflexdev/cilanding:main' > .images.env
fi

chmod 600 .env .images.env

if [[ ! -e /swapfile ]]; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
fi

install -m 0644 "${source_dir}/swapfile.swap" /etc/systemd/system/swapfile.swap
install -m 0755 "${source_dir}/lernium-deploy" /usr/local/sbin/lernium-deploy
systemctl daemon-reload
systemctl enable --now swapfile.swap

docker compose --env-file .env --env-file .images.env config --quiet
docker compose --env-file .env --env-file .images.env pull
docker compose --env-file .env --env-file .images.env up -d --remove-orphans
