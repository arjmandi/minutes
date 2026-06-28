#!/bin/sh
# In-compose scheduler for the single-box deployment.
# Runs the orphan-chunk reconciler on a short interval and the GDPR retention purge daily.
# (In a Kubernetes / cron-based deployment these would be scheduled jobs instead.)
set -e

RECONCILE_EVERY_S="${RECONCILE_EVERY_S:-900}"   # 15 min
RETENTION_EVERY_S="${RETENTION_EVERY_S:-86400}" # 24 h
# Seed to "now" so the first retention purge fires one full interval after start, not on every
# (re)start — reconcile still runs immediately each loop.
last_retention="$(date +%s)"

echo "[scheduler] start (reconcile=${RECONCILE_EVERY_S}s retention=${RETENTION_EVERY_S}s)"
while true; do
	python -m app.jobs.reconcile || echo "[scheduler] reconcile failed (will retry next cycle)"
	now="$(date +%s)"
	if [ "$((now - last_retention))" -ge "$RETENTION_EVERY_S" ]; then
		python -m app.jobs.retention || echo "[scheduler] retention failed (will retry next cycle)"
		last_retention="$now"
	fi
	sleep "$RECONCILE_EVERY_S"
done
