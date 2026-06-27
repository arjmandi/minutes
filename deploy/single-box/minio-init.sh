#!/bin/sh
# One-shot MinIO bootstrap for the single-box deployment:
#   1. wait for MinIO, 2. create the audio bucket, 3. mint an APP-SCOPED service account whose
#   policy allows only Get/Put/Delete on minutes-audio/* — so the backend never holds the MinIO
#   root credential (least privilege; limits blast radius of an app compromise).
set -e

until mc alias set local "http://minio:9000" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; do
	echo "waiting for minio"
	sleep 2
done

mc mb --ignore-existing local/minutes-audio

cat > /tmp/minutes-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::minutes-audio/*"]
    }
  ]
}
EOF

# Idempotent: only create the service account if it does not already exist (survives redeploys).
if ! mc admin user svcacct info local "$MINUTES_S3_ACCESS_KEY" >/dev/null 2>&1; then
	mc admin user svcacct add \
		--access-key "$MINUTES_S3_ACCESS_KEY" \
		--secret-key "$MINUTES_S3_SECRET_KEY" \
		--policy /tmp/minutes-policy.json \
		local "$MINIO_ROOT_USER"
fi

echo "minio init done (bucket + app-scoped service account)"
