#!/bin/sh
set -e

# Run DB migrations on boot when enabled. For multi-replica deployments, set MINUTES_RUN_MIGRATIONS=0
# and run `alembic upgrade head` once as a separate step to avoid concurrent-migration races; for a
# single replica (e.g. the single-box deployment) leaving it on is fine.
if [ "${MINUTES_RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
fi

exec "$@"
