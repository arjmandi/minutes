---
sidebar_position: 1
title: "Deploying minutes"
---

# Deploying minutes

**minutes** is a self-hosted, EU-friendly, multi-user tool for **live meeting transcription and translation**. It has three parts:

1. a **web app** (the product UI),
2. a **Chrome capture extension** that streams a Google Meet or Microsoft Teams tab's audio, and
3. a **FastAPI backend** that runs as a single Docker Compose stack — a Caddy TLS edge, the backend, Postgres, Valkey (Redis), a MinIO object store, and a small scheduler.

The pipeline is: capture the meeting tab's audio → **Soniox** (speech-to-text; English, German, Persian) → optional **Anthropic / Claude** translation → live transcript in the web app. Only two external services ever receive data — **Soniox** (audio for STT) and **Anthropic** (text for translation). Everything else stays on your box.

This page walks you through standing up the full stack on a single VPS. When you're done, you'll create your first user in [Managing users](/admin/users), and the complete environment-variable reference lives in [Configuration](/admin/configuration).

:::note This is a low-cost, single-box deployment
It runs the **entire** stack on one node: single-drive Postgres and MinIO, **no replication, no failover, no automated backups**. A disk or host failure can lose data — an accepted trade-off for a showcase/low-cost deploy. For production HA, move the datastores to managed/replicated services across nodes; the application code is unchanged.
:::

## Prerequisites

Before you start, line up the following:

- **A VPS.** 2 vCPU / **8 GB RAM** recommended (e.g. a Hostinger KVM2), Ubuntu 22.04 or 24.04. Pick an **EU region** (Germany or Lithuania) so meeting data stays in the EU for GDPR.
- **Docker Engine + the Compose plugin** on that VPS.
- **A domain with a DNS `A` record pointing at the VPS IP** — for example `meet.example.com`. A real domain is **required**, not optional (see below).
- **Inbound TCP ports 80 and 443 reachable from the internet.** Let's Encrypt validates over them and Caddy serves HTTPS / `wss` on them. Open both in your provider's firewall (and `ufw allow 80,443/tcp` if `ufw` is on). No other port should be public.
- **A Soniox API key** for live-capture speech-to-text. Get one at [soniox.com](https://soniox.com).
- *(Optional)* An Anthropic key is **not** set at the server level for normal use — translation uses each user's own key, set per-user in the web app Settings. Get one at [console.anthropic.com](https://console.anthropic.com).

:::warning You need a domain, not just an IP
The capture extension opens a **secure WebSocket** (`wss://`) from a secure browser context. A secure WebSocket requires TLS, and TLS (via Caddy + Let's Encrypt) requires a **real, public domain name**. A bare IP or `http://localhost` only works for local development (`ws://`, no TLS) — it will **not** work with the extension or the HTTPS web app.
:::

Install Docker on a fresh box:

```bash
curl -fsSL https://get.docker.com | sh
```

## Step-by-step

### 1. Clone the repo and enter the deploy directory

```bash
git clone <your-fork-of-minutes>
cd minutes/deploy/single-box
```

Everything below runs from `deploy/single-box`.

### 2. Create your `.env`

```bash
cp .env.example .env
```

You'll fill this in over the next two steps. **Never commit `.env`.**

### 3. Generate secrets

The backend runs with `MINUTES_APP_ENV=prod`, which **fails closed**: it refuses to start on a weak or default secret. Both secrets must be a strong, non-default value of at least 32 bytes. Generate one for each:

```bash
openssl rand -hex 32      # paste into MINUTES_AUTH_SECRET
openssl rand -hex 32      # paste into MINUTES_SECRET_KEY
```

- `MINUTES_AUTH_SECRET` — signs capability tokens.
- `MINUTES_SECRET_KEY` — the AES-256-GCM key that encrypts each user's stored provider keys.

### 4. Fill in the rest of `.env`

Set at minimum:

```ini
DOMAIN=meet.example.com          # your domain; its A record must point at this VPS
MINUTES_AUTH_SECRET=...           # from openssl rand -hex 32
MINUTES_SECRET_KEY=...            # from openssl rand -hex 32
MINUTES_SONIOX_API_KEY=...        # optional: fallback Soniox key for live capture (users normally bring their own)
MINUTES_SONIOX_REGION=us          # us | eu — region for that fallback key (eu keeps audio in the EU)

POSTGRES_PASSWORD=...             # any strong value
MINIO_ROOT_PASSWORD=...           # >=8 chars (used only by the MinIO bootstrap, never by the app)
MINUTES_S3_SECRET_KEY=...         # >=8 chars (app-scoped MinIO service account)
```

`MINUTES_SONIOX_API_KEY` is **optional** — a fallback used for **live capture** only when a user hasn't set their own Soniox key, with `MINUTES_SONIOX_REGION` (`us`/`eu`) as that key's data region. Normally each user brings their own Soniox key + region (and their own Anthropic key) in the web app Settings, so you can leave the server key empty to require that. See [Configuration → keys](/admin/configuration#stt-and-translation-keys).

:::tip Optional settings have safe defaults
You can leave the optional knobs at their template values to start. Notably the shipped `.env.example` sets `MINUTES_REQUIRE_CONSENT=true` (the GDPR-safe choice; the app's built-in default is `false`) and `MINUTES_RETENTION_DAYS=90`. The full reference is in [Configuration](/admin/configuration).
:::

### 5. Bring the stack up

```bash
docker compose up -d --build
docker compose ps
```

Boot order is health-gated automatically: Postgres, Valkey, and MinIO come up first, a one-shot `minio-init` service creates the audio bucket and an app-scoped service account, a one-shot **`migrate`** service runs `alembic upgrade head` once, then the `backend` starts (and only becomes healthy once `/readyz` — DB + Redis — is green), and finally Caddy fetches a Let's Encrypt cert for `$DOMAIN`.

### 6. Verify

Watch Caddy obtain the certificate, then hit the health endpoints:

```bash
DOMAIN=$(grep ^DOMAIN= .env | cut -d= -f2)   # so the curls below resolve
docker compose logs -f caddy                 # watch Let's Encrypt cert issuance
curl -fsS https://$DOMAIN/healthz            # liveness
curl -fsS https://$DOMAIN/readyz             # readiness (db + redis)
```

Then open **`https://$DOMAIN/`** in a browser — you should see the product. You're deployed.

:::note No public signup
There is no self-service registration. The next step is to create your first user (which is automatically an admin) with the admin CLI — see [Managing users](/admin/users).
:::

## Updating to a new build

Pull the latest code and re-deploy. The one-shot `migrate` service re-runs `alembic upgrade head`, so schema changes are applied automatically:

```bash
git pull
docker compose up -d --build
```

## The marketing landing path-split

Caddy serves a path-split on the single domain:

- `/` serves a static **marketing landing** page (mounted from a host directory, kept out of the repo), and
- `/app`, `/api`, `/assets`, `/shared`, `/ingest`, `/healthz`, and `/readyz` go to the product backend.

If **no** landing directory is mounted — the stock open-source self-host case — Caddy falls back to serving the app at `/` too, so a plain deploy "just works" with the product at the root. To point at your own landing files, set **`LANDING_DIR`** in `.env` to the host path (it defaults to `/opt/minutes-site`).

## Where to go next

- **[Managing users](/admin/users)** — create your first (admin) user and manage accounts with the admin CLI.
- **[Configuration](/admin/configuration)** — the full `.env` reference: translation targets, concurrency, consent gating, retention, and more.
