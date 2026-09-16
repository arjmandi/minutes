#!/bin/sh
# In-compose scheduler for the single-box deployment.
# Processes upload-transcription jobs on a short tick, runs the orphan-chunk reconciler on a
# medium interval, and the GDPR retention purge daily.
# (In a Kubernetes / cron-based deployment these would be scheduled jobs instead.)
set -e

UPLOAD_EVERY_S="${UPLOAD_EVERY_S:-20}"          # poll the upload-transcription queue
RECONCILE_EVERY_S="${RECONCILE_EVERY_S:-900}"   # 15 min
RETENTION_EVERY_S="${RETENTION_EVERY_S:-86400}" # 24 h
# Seed retention to "now" so the first purge fires one full interval after start, not on every
# (re)start. Reconcile runs on the first tick (last_reconcile=0).
last_reconcile=0
last_retention="$(date +%s)"

echo "[scheduler] start (upload=${UPLOAD_EVERY_S}s reconcile=${RECONCILE_EVERY_S}s retention=${RETENTION_EVERY_S}s)"
while true; do
	python -m app.jobs.transcription || echo "[scheduler] upload worker failed (will retry next tick)"
	now="$(date +%s)"
	if [ "$((now - last_reconcile))" -ge "$RECONCILE_EVERY_S" ]; then
		python -m app.jobs.reconcile || echo "[scheduler] reconcile failed (will retry next cycle)"
		last_reconcile="$now"
	fi
	if [ "$((now - last_retention))" -ge "$RETENTION_EVERY_S" ]; then
		python -m app.jobs.retention || echo "[scheduler] retention failed (will retry next cycle)"
		last_retention="$now"
	fi
	sleep "$UPLOAD_EVERY_S"
done
