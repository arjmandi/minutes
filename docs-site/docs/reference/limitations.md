---
sidebar_position: 2
title: "Limitations & known gaps"
---

# Limitations & known gaps

minutes is an open-source, self-hosted product. We'd rather you know its edges up front than discover them in production. This page is the honest list: what the default deployment does *not* do, and which design choices are deliberate trade-offs.

If something here is a blocker for your use case, most of it is fixable by swapping infrastructure — the application code is unchanged, only the surrounding setup differs.

## The default single-box deployment is a showcase, not HA

The [single-box deployment](/admin/deploy) runs the **entire** stack on one VPS with `docker compose`: a Caddy TLS edge, the FastAPI backend, Postgres, Valkey, MinIO, and a small scheduler. It is intentionally **not** highly available.

:::danger Data loss is possible
- **One node.** The VPS, Postgres, Valkey, and MinIO are each a single point of failure.
- **Single-drive Postgres and MinIO.** A disk or host failure can lose data.
- **No replication, no failover, no PITR, no cross-region.**
- **No automated backups.** The deployment ships none — see `deploy/single-box/README.md` for the nightly `pg_dump` + bucket-mirror cron you can add yourself on the host.

This is an accepted trade-off for a low-cost showcase. **Do not put data you can't afford to lose on it without your own off-box backups.**
:::

Archived audio is best-effort: the two-phase write + reconciler already model loss via a `LOST` chunk state, so a transcript can survive even if its audio archive does not.

:::tip For production
Move the datastores to managed (or replicated) Postgres + Redis + S3-compatible object storage across multiple nodes, with backups/PITR. Scaling the app past one replica also needs a Caddy upstream change (the admission cap is already Redis-shared, so it's safe once the edge load-balances). The application code does not change. See `deploy/single-box/README.md` → *"What this is NOT"*.
:::

## No public signup — accounts are admin-created

There is no self-service registration. An operator creates every user with the admin CLI:

```bash
docker compose exec backend python -m app.admin create-user --email you@org.com [--admin]
```

The **first** user created is automatically an admin. Other subcommands: `set-password --email`, `delete-user --email`, `list-users`. See [Managing users](/admin/users) for the full workflow.

## The extension needs a real domain + TLS — no bare IP

The Chrome capture extension opens a **secure WebSocket (`wss://`)** from a secure browser context. That requires TLS, which (via Caddy + Let's Encrypt) requires a real public **domain** with an `A` record and inbound TCP **80 and 443** reachable from the internet.

:::warning A bare IP or `http://localhost` will not work for capture
Browsers refuse to open an insecure WebSocket from an HTTPS page, and Let's Encrypt cannot issue a certificate for a bare IP. `ws://` over `http://localhost` works only for local development of the app itself — never for the extension against a server. See [Deploy](/admin/deploy) for the domain/DNS prerequisites.
:::

## Each user brings their own provider keys

Only two external services ever receive your data: **Soniox** (audio, for speech-to-text) and **Anthropic** (text, for translation). Keys are configured as follows:

| Feature | Key used | Where it's set |
| --- | --- | --- |
| Live-capture STT | The **server's** Soniox key (`MINUTES_SONIOX_API_KEY` in `.env`) | Operator, once |
| Audio **upload** transcription | The **owner's own** Soniox key | Per user, in **Settings** |
| Translation (live, upload, on-demand) | The **owner's own** Anthropic key | Per user, in **Settings** |

Consequences:

- **No Anthropic key → translation is simply off.** Transcription still works; you just get no translated text.
- **No personal Soniox key → transcription needs a fallback.** Audio-upload transcription always uses the user's own Soniox key, so it won't run without one. Live capture uses the user's key too — it only works keyless if the admin set a fallback `MINUTES_SONIOX_API_KEY`.
- Each user's Soniox key carries a **region** (US/EU); audio is processed on the matching Soniox endpoints. Get keys at [soniox.com](https://soniox.com) and [console.anthropic.com](https://console.anthropic.com). Set them in the web app's **Settings**; they are stored encrypted (AES-256-GCM).

You also need to have the tab open under your own account — for a Meet/Teams call, join it yourself; for a video, webinar, or podcast, just open it. The extension captures **that browser tab's audio** (the **Online stream**). It can optionally also capture your own microphone (the **Host mic**, off by default, toggled in the extension). minutes does not join meetings as a bot.

## Two capture sources mean two concurrent Soniox connections

A capture can include two independent sources kept fully separate — the **Online stream** (the browser tab) and the **Host mic** (your own microphone, off by default). When both are on, the capture opens **two concurrent realtime Soniox connections**, both on the **capturing user's own** Soniox key (or the server fallback `MINUTES_SONIOX_API_KEY` if they have none).

:::warning A Soniox concurrency cap can reject the second source
If the Soniox plan behind the key caps simultaneous realtime connections, the **second** connection is rejected. minutes degrades gracefully: the rejected source surfaces an **actionable error** (in the extension popup chip and in the web app) telling you to check or upgrade your Soniox plan, while the **other source keeps recording**. So a user who runs both Online stream *and* Host mic needs Soniox concurrency for **two** streams. See [Troubleshooting](/reference/troubleshooting) for the symptom and fix.
:::

## Tokens are short-lived and cannot be revoked early

Capability tokens (for the viewer) and ingest tokens (for capture) are signed and time-bounded. The default capability TTL is **3600 seconds** (`MINUTES_AUTH_TOKEN_TTL_S`).

:::warning No early revocation
A minted token **cannot be revoked before it expires.** If one leaks, it is valid until its TTL elapses. Mitigate by minting **short** TTLs and scoping narrowly — prefer a single meeting (`--meetings meet:my-meeting-id`) over `--meetings '*'`, and reserve `--admin` (required for `DELETE /meetings/{id}` erasure) for narrow operational use. See the token-minting section of `deploy/single-box/README.md`.
:::

(Web sessions and the extension's device token are different: those are DB-backed and *do* expire on their own schedule — a 15-minute access JWT, a 30-day web refresh, and a 30-day extension device token.)

## One output language per meeting

A meeting has **one** translation target language at a time. Supported languages are English (`en`), German (`de`), and Persian (`fa`, right-to-left).

:::note Mid-meeting language changes apply to the next session
If you change a meeting's output language (or its custom prompt/model) while capture is running, the change takes effect on the **next** capture session — not retroactively, and not for lines already streaming in the current one. The per-line **"translate this line"** on-demand action is available any time if you need a one-off in a different rendering.
:::

## The Soniox async (upload) path needs validation against your account

Audio upload transcription is processed out-of-band by the scheduler (roughly every 20 seconds), with a default ceiling of ~300 MB per file (`MINUTES_UPLOAD_MAX_BYTES`, default `314572800`) and at most 2 concurrent jobs (`MINUTES_UPLOAD_MAX_CONCURRENT`, default `2`).

:::warning Validate the upload path on your own Soniox account
The Soniox **async** (file upload) path is wired up but should be **validated against your own Soniox account** before you rely on it in production — async transcription behavior and quotas can vary by account. Live capture uses the synchronous streaming path and is the primary, well-exercised flow.
:::

## Where to confirm specifics

Everything above is grounded in two files in the repo — read them when you need exact values:

- **`deploy/single-box/README.md`** — topology, prerequisites, the HA trade-offs, token minting, and backups.
- **`app/config.py`** — every `MINUTES_*` setting and its default (TTLs, upload limits, retention, concurrency).

For the features that *do* work and how to use them, see the [user guide](/) and [admin pages](/admin/deploy).
