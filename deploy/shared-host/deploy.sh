#!/bin/sh
# Runs on the shared host as the deploy user from ~/minutes after the workflow synced
# this directory and pushed the image. IMAGE_TAG selects the image; the previous tag is
# kept in .image-tag for rollback (`IMAGE_TAG=<old> sh deploy.sh`).
set -eu
cd "$(dirname "$0")"
: "${IMAGE_TAG:?IMAGE_TAG is required}"
export IMAGE_TAG
[ -f .env ] || { echo ".env is missing in $(pwd) (see .env.example)" >&2; exit 1; }
docker network inspect oddproof-edge >/dev/null 2>&1 || {
  echo "ERROR: Docker network oddproof-edge is missing; start the oddproof-edge project first." >&2
  exit 1
}
umask 077
mkdir -p backups landing docs
docker compose config --quiet
docker compose pull -q backend web
docker compose up -d --wait postgres redis minio
if docker compose ps --status running --services | grep -qx backend; then
  backup="backups/pre-$(date -u +%Y%m%dT%H%M%SZ).dump"
  docker compose exec -T postgres pg_dump -U minutes -Fc minutes > "$backup"
  test -s "$backup" && echo "database backup saved: $backup"
fi
docker compose up -d --wait   # migrate runs once; backend and web wait for it
docker compose exec -T web caddy validate --config /etc/caddy/Caddyfile >/dev/null
echo "$IMAGE_TAG" > .image-tag
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
