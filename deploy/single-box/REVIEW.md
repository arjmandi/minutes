# Adversarial review — single-box deploy artifacts

Workflow `wgtss25p5`: 3 dimensions (security, correctness, ops/docs) → adversarial verify, scoped
so the **intentional** single-box trade-offs (no HA, single-drive PG/MinIO, no failover, no
automated backups, accepted data-loss) were explicitly out of scope. 13 candidates → **13 confirmed**
(after dedup: 4 medium + the rest low). All addressed:

| Sev | Finding | Resolution |
|-----|---------|------------|
| med | App was handed the MinIO **root** credential | `minio-init.sh` now mints an **app-scoped service account** (policy: Get/Put/Delete on `minutes-audio/*` only); the backend uses `MINUTES_S3_ACCESS_KEY`/`SECRET_KEY`, never root. **Validated:** scoped-key PUT/HEAD/DELETE round-trip succeeds. |
| med | `MINUTES_REQUIRE_CONSENT=false` default for an EU recording tool | Default flipped to **`true`** (`.env.example` + compose `:-true`), with a lawful-basis comment. |
| med | README minted a 24h `['*']` wildcard token as the default example | Example now **least-privilege**: `ttl_s=3600`, `meetings=['meet:my-meeting-id']`, with a note that tokens can't be revoked before expiry and `['*']`/`admin` are for narrow use. |
| med | Port 80/443 reachability + ACME firewall reqs undocumented | Added a Prerequisites bullet (open 80+443; watch `caddy` logs for cert issuance). |
| med/low | `minio:latest` / `mc:latest` unpinned (non-reproducible) | Pinned `minio/minio:RELEASE.2025-04-22…`, `minio/mc:RELEASE.2025-04-16…`, `caddy:2.8`; tags confirmed to exist and re-validated. |
| low | Docker published ports bypass `ufw` — no caveat | Added a "Network exposure" note: only 80/443 public; never add host `ports:` to datastores. |
| low | No HSTS / security headers at the edge | Added `Strict-Transport-Security`, `X-Content-Type-Options`, `Referrer-Policy` to the Caddyfile. |
| low | Scheduler ran the retention purge on **every** (re)start | `scheduler.sh` seeds `last_retention="$(date +%s)"` → first purge one interval after start. |
| low | Bind-mounted `scheduler.sh` edits don't take effect on `up -d` | Documented `docker compose up -d --force-recreate scheduler`. |
| low | README curls used `$DOMAIN` (only set in `.env`) | Added `DOMAIN=$(grep ^DOMAIN= .env | cut -d= -f2)` before the curls. |
| low | `MINUTES_TRANSLATION_TARGETS=["en"]` yields no output for English speech | Default → `["de"]` with a same-as-source-is-skipped comment. |

**Validation:** `docker compose config` valid; full stack boots in order on the pinned images
(postgres/redis/minio healthy → `minio-init` creates bucket + scoped svcacct → `migrate` runs
alembic → `backend` healthy `/readyz=200` → `scheduler` runs reconcile+retention), and the scoped
MinIO key performs a full PUT/HEAD/DELETE round-trip against `minutes-audio`.
