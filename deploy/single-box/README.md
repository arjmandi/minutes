# minutes — single-box deployment (Hostinger KVM2)

Runs the **entire** service on one VPS with `docker compose`: a Caddy TLS edge, the FastAPI
backend, Postgres, Valkey, MinIO (S3-compatible audio store), and a small cron scheduler.

> **This is a low-cost showcase deployment.** It is intentionally **not** highly available: one
> node, single-drive Postgres/MinIO, **no replication, no failover, no automated backups**. A disk
> or host failure can lose data — that is an accepted trade-off here. For a production / HA setup,
> run managed (or replicated) Postgres + Redis + S3-compatible object storage across multiple nodes
> with backups/PITR — the application code is unchanged, only the surrounding infra differs.

## Topology (one box)

```
                   :443 wss/https
  capture client ───────────────►  Caddy  ──►  backend (uvicorn :8000)
  (Chrome ext)                     (TLS,        │   ├── Postgres   (source of truth)
  web viewer                        WS)         │   ├── Valkey     (admission + fan-out + control)
                                                │   └── MinIO      (audio archive, single drive)
                                          scheduler ── reconcile (15m) + retention (daily)
```

## Sizing

KVM2 = 2 vCPU / 8 GB. The pipeline is I/O-bound (STT is remote at Soniox, translation remote at
Claude), so concurrency is gated mainly by RAM + the per-call audio work. `MINUTES_MAX_CONCURRENT_CALLS`
defaults to **5**; if the box struggles under real load, lower it in `.env` and `docker compose up -d`.
Memory limits are set per service in the compose file (Postgres 1.5G, MinIO 1G, backend 1G, etc.).

## Prerequisites

1. A Hostinger **KVM2** VPS in an **EU** region (Germany or Lithuania) running Ubuntu 22.04/24.04.
2. **Docker Engine + compose plugin** on the VPS.
3. A **domain** with an `A` record pointing at the VPS IP — required for `wss://` (browsers won't
   open an insecure WebSocket from an HTTPS page, and the capture extension uses `wss://`).
4. A **Soniox** API key (STT) and optionally an **Anthropic** key (translation).
5. **Inbound TCP 80 and 443 reachable from the internet** — Let's Encrypt validates over them and
   Caddy serves HTTPS/`wss` there. Open both in the Hostinger panel firewall (and `ufw allow
   80,443/tcp` if `ufw` is on). No other port should be public.

Install Docker on a fresh box:

```bash
curl -fsSL https://get.docker.com | sh
```

## Deploy

```bash
git clone <your-fork-of-minutes> && cd minutes/deploy/single-box
cp .env.example .env
# Fill in .env. Generate the auth secret + passwords:
openssl rand -hex 32      # paste into MINUTES_AUTH_SECRET
# Required: DOMAIN, MINUTES_SONIOX_API_KEY, POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD,
#           MINUTES_S3_SECRET_KEY   (optional: MINUTES_ANTHROPIC_API_KEY)

docker compose up -d --build
docker compose ps
```

Boot order is handled by health-gated `depends_on`: Postgres/Valkey/MinIO come up, `createbuckets`
makes the audio bucket, `migrate` runs `alembic upgrade head` once, then `backend` starts and only
becomes healthy when `/readyz` (DB + Redis) is green, and finally Caddy fetches a cert for `$DOMAIN`.

### Verify

```bash
DOMAIN=$(grep ^DOMAIN= .env | cut -d= -f2)   # so the curls below resolve
docker compose logs -f caddy              # watch Let's Encrypt cert issuance
curl -fsS https://$DOMAIN/healthz         # liveness
curl -fsS https://$DOMAIN/readyz          # readiness (db+redis)
```

Open `https://$DOMAIN/` for the live web viewer.

## Get a capability token

In `prod` the dev token-mint endpoint is disabled. Mint tokens out-of-band with the running config:

```bash
# All meetings, 1-day token (good for testing the viewer):
docker compose exec -T backend python -m app.mint_token --meetings '*' --ttl 86400
# Least privilege: scope to one meeting with a short TTL:
docker compose exec -T backend python -m app.mint_token --meetings meet:my-meeting-id --ttl 3600
# Admin scope (required for DELETE /meetings/{id} erasure):
docker compose exec -T backend python -m app.mint_token --meetings '*' --ttl 3600 --admin
```

Point capture clients (see `capture/`) at `wss://$DOMAIN/ingest` with that token — the
`platform:external_meeting_id` in `meetings` must match what the client captures. Tokens **cannot be
revoked before they expire**, so keep TTLs short. Reserve `meetings=['*']` / `admin=True` (the
latter required for `DELETE /meetings/{id}` erasure) for narrow operational use.

## Operating it

- **Logs:** `docker compose logs -f backend caddy`
- **Update to a new build:** `git pull && docker compose up -d --build` (the `migrate` one-shot
  re-runs `alembic upgrade head`).
- **Tune concurrency:** edit `MINUTES_MAX_CONCURRENT_CALLS` in `.env`, then `docker compose up -d`.
- **Editing `scheduler.sh`:** it is bind-mounted, so after a change run
  `docker compose up -d --force-recreate scheduler` (a plain `up -d` won't pick it up).
- **Network exposure:** only Caddy's 80/443 are public; Postgres/Valkey/MinIO have no host ports and
  stay on the internal Docker network. Docker's published ports **bypass `ufw`** (rules land in the
  `DOCKER` chain), so never add a host `ports:` mapping to the datastores — use `docker compose
  exec` for admin access instead.
- **Scaling the app** beyond one replica needs a Caddy upstream change (load-balance across
  containers); the admission cap is already Redis-shared, so it's safe to scale once the edge is.

## Backups (your responsibility here)

This deployment ships **no** automated backups. If the data matters even a little, add off-box
copies via cron on the host:

```bash
# nightly Postgres dump to elsewhere
docker compose exec -T postgres pg_dump -U minutes minutes | gzip > /backups/minutes-$(date +%F).sql.gz
# mirror the audio bucket off-box (configure an external S3/rsync target)
```

## What this is NOT

No HA, no failover, no PITR, no cross-region. The single VPS, Postgres, Valkey, and MinIO are each
a single point of failure. Treat archived audio as best-effort (the two-phase write + reconciler
already model loss via the `LOST` chunk state). When you outgrow this, move the datastores to
managed/replicated services across multiple nodes — the application is unchanged.
