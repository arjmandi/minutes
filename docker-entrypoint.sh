#!/bin/sh
set -e

# Run DB migrations on boot when enabled. For multi-replica Kubernetes, set MINUTES_RUN_MIGRATIONS=0 on
# the Deployment and run migrations once via the migrate Job (deploy/k8s/migrate-job.yaml) to
# avoid concurrent `alembic upgrade` races; for a single replica / Droplet, leaving it on is fine.
if [ "${MINUTES_RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
fi

exec "$@"
