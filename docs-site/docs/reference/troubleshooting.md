---
sidebar_position: 3
title: "Troubleshooting"
---

# Troubleshooting

A symptom-by-symptom guide to the problems people hit most often with **minutes**. Each entry tells
you what you'll see, why it happens, and how to fix it. Operator-only steps (running commands on the
box) are marked as such.

If your issue isn't here, the fastest way to learn what's wrong is almost always the backend and
Caddy logs:

```bash
docker compose logs -f backend caddy
```

---

## The extension says "No meeting on this tab"

**What you see:** The extension popup shows *No meeting on this tab* with *Open a Google Meet or
Teams meeting to capture*, and the **Start recording** button is greyed out.

**Why:** The extension captures audio from the **active browser tab**. It reads the meeting straight
from that tab's URL, so it only sees a meeting when a real Google Meet or Microsoft Teams meeting tab
is in front.

**Fix:**

1. Open (or click into) the actual Meet / Teams meeting tab so it's the focused tab.
2. Click the toolbar icon again to reopen the popup — it re-reads the active tab and should now show
   the meeting platform and ID, with **Start recording** enabled.
3. If you opened the meeting in a *different* window, focus that window first.

:::note
You also have to be a real participant in the meeting under your own Google / Microsoft account. The
extension only forwards the tab's audio; it does not join the meeting for you.
:::

---

## Cannot connect / the WebSocket (wss) fails

**What you see:** Sign-in or recording fails to connect, the browser console shows a failed
`wss://` connection, or the web app won't load over HTTPS.

**Why:** The capture extension opens a **secure** WebSocket (`wss://`) from a secure context.
Browsers refuse to open an insecure WebSocket from an HTTPS page, so this requires real TLS — and TLS
(via Caddy / Let's Encrypt) requires a real public **domain**, not a bare IP.

**Fix (operator):**

- **Use a domain, not an IP.** `wss://` and HTTPS need a certificate, and certificates are issued for
  domain names. A bare IP or `http://localhost` only works for local development (plain `ws://`, no
  TLS).
- **Make sure ports 80 and 443 are public.** Let's Encrypt validates over them and Caddy serves
  HTTPS / `wss` there. Open both in your provider's firewall (and `ufw allow 80,443/tcp` if `ufw`
  is on). No other port needs to be public.
- **Check the certificate was actually issued** by watching Caddy:

  ```bash
  docker compose logs -f caddy
  ```

  You're looking for successful certificate issuance for your `$DOMAIN`. Errors here almost always
  trace back to DNS or a blocked port (see [TLS certificate not issued](#tls-certificate-not-issued)).

- **Confirm the backend is reachable** behind the edge:

  ```bash
  DOMAIN=$(grep ^DOMAIN= .env | cut -d= -f2)
  curl -fsS https://$DOMAIN/healthz   # liveness
  curl -fsS https://$DOMAIN/readyz    # readiness (db + redis)
  ```

See [Domain &amp; TLS](/admin/deploy) for the full deployment story.

---

## TLS certificate not issued {#tls-certificate-not-issued}

**What you see:** `docker compose logs -f caddy` shows certificate errors; HTTPS doesn't come up.

**Why:** Let's Encrypt has to prove your box owns the domain over the public internet, on ports 80
and 443. If DNS doesn't point at the box, or those ports aren't reachable, issuance fails.

**Fix (operator):**

1. **DNS:** make sure the domain's `A` record points at the VPS's public IP, and that it has
   propagated.
2. **Ports:** confirm inbound **TCP 80 and 443** are reachable from the internet (provider firewall
   plus `ufw allow 80,443/tcp` if applicable).
3. Re-watch issuance:

   ```bash
   docker compose logs -f caddy
   ```

:::warning
Don't publish host ports for Postgres / Valkey / MinIO to "debug" connectivity. Docker's published
ports bypass `ufw`. Only Caddy's 80/443 should ever be public — use `docker compose exec` for admin
access to the datastores.
:::

---

## Sign-in fails

**What you see:** The web app or the extension popup rejects your email and password with *invalid
email or password*.

**Why:** There is **no public signup**. An account only exists if an admin created it. Sign-in also
fails if the extension is pointed at the wrong server.

**Fix:**

- **Confirm your account exists.** Ask your admin to create it, or to list users (operator):

  ```bash
  docker compose exec backend python -m app.admin list-users
  ```

  See [creating the first admin](#no-admin-user-yet) below if no one has an account yet.

- **Check the server URL (extension).** Open the extension's settings (the gear icon in the popup)
  and verify the **Server URL** is your real server (e.g. `https://your-domain`), not an old or local
  address. The popup signs in against exactly that server.

- **Forgot your password?** There's no self-service reset. An admin can set a new one (this also
  signs you out everywhere):

  ```bash
  docker compose exec backend python -m app.admin set-password --email you@org.com
  ```

---

## No admin user yet {#no-admin-user-yet}

**What you see:** A brand-new install and no one can sign in.

**Why:** minutes ships with zero accounts. The very **first** user you create is automatically an
admin.

**Fix (operator):** create the first user on the box.

```bash
docker compose exec backend python -m app.admin create-user --email you@org.com
```

The first user is auto-admin, so you don't need `--admin` for it. For later admins, add `--admin`.
Full account commands: `create-user`, `set-password --email`, `delete-user --email`, `list-users`.

---

## Translation does nothing

**What you see:** The transcript appears, but no translated text shows up.

**Why:** Translation uses **your own Anthropic key**, and it must be enabled for the meeting. With no
key, translation is simply off.

**Fix:**

1. **Set your Anthropic key** in the web app **Settings**. Get one at
   [console.anthropic.com](https://console.anthropic.com). Keys are stored encrypted and never
   returned in any response — if you're unsure whether it saved, Settings shows whether a key is set.
2. **Enable translation on the meeting** and pick an output language (English, German, or Persian).
   You can also use the on-demand *translate this line* action.

:::note
Translation is per-user. Your key powers translation for meetings you own; it is never shared with
other users.
:::

---

## Host mic shows an error / only one source records

**What you see:** You started a capture with both the **Online stream** and the **Host mic** on, but
only one records. The other source shows an **error** — a red status chip in the extension popup and a
matching error in the web app — often mentioning your Soniox plan or connection limit.

**Why:** With both sources on, a capture opens **two concurrent realtime Soniox connections**, both on
**your own** Soniox key (or the server fallback). If your Soniox plan caps simultaneous realtime
connections, Soniox **rejects the second** one. minutes keeps the source that did connect recording and
surfaces an actionable error on the one that was refused, rather than failing the whole capture.

**Fix:**

- **Check / upgrade your Soniox plan's concurrency.** Running both Online stream and Host mic needs your
  Soniox plan to allow **two** simultaneous realtime connections. Raise the limit at
  [soniox.com](https://soniox.com), and the second source will connect on the next capture.
- **Or capture one source at a time.** If you don't need both, turn the **Host mic** off (it's off by
  default) so the capture uses a single connection.

:::note
This is per-user and tied to the key in use. If a user is on the server fallback
(`MINUTES_SONIOX_API_KEY`), it's that key's plan whose concurrency applies. See
[Limitations](/reference/limitations) for the design rationale.
:::

---

## Quiet speech is being dropped from the Host mic

**What you see:** Soft or distant speech on the **Host mic** doesn't make it into the transcript, while
normal-volume speech transcribes fine.

**Why:** **Silence-suspend** (a cost-saver in the extension settings, **on by default**) pauses the Host
mic's Soniox stream during sustained silence to cut billed seconds, and resumes on speech. Very quiet
speech can read as silence and be skipped. Timing is unaffected — segment timestamps still track real
meeting time because dropped silence is added back server-side — so this only ever drops words, never
shifts the timeline.

**Fix:** Open the **extension settings** (options page) and turn **silence-suspend off** for sessions
with quiet speakers. The Host mic then streams continuously (at the cost of more billed Soniox seconds).

---

## Host mic is quiet, clipped, or blocked

**What you see:** The **Host mic** source is too quiet, the host's voice cuts in and out / sounds clipped,
or the mic produces nothing at all.

**Why and fix:**

- **Wrong device or low level.** The mic device and a **live level test** are in the **extension
  settings** (options page). Open them, pick the right input, and watch the level meter while you speak to
  confirm it's actually capturing.
- **Echo cancellation is clipping the voice.** The extension's **echo-cancellation** toggle is **off by
  default**, and should usually stay off: with the meeting tab playing through **speakers**, AEC treats
  the host's own voice as echo and can clip it. Leave it **off on headphones** too. If the host sounds
  chopped, confirm this toggle is off in the extension settings.
- **The mic is blocked by Chrome.** If the browser denied microphone access, no Host mic audio is
  captured. Re-enable it at `chrome://settings/content/microphone` (allow your minutes server's site),
  then start the capture again.

:::note
The Host mic is a separate source from the Online stream. A mic problem only affects the Host mic
track — the Online stream (the tab's audio) keeps recording independently.
:::

---

## Audio upload is stuck or failed

**What you see:** An uploaded file sits in a pending state, never finishes, or comes back with an
error.

**Why and fix:**

- **No Soniox key.** Upload transcription uses **your own Soniox key** (unlike live capture, which
  uses the server's key). Set it in the web app **Settings**. Get one at
  [soniox.com](https://soniox.com).
- **File too large.** The cap is ~300 MB per file (`MINUTES_UPLOAD_MAX_BYTES`, default
  `314572800`). A bigger file is rejected with *file too large* (HTTP 413). Trim or re-encode it.
- **Wrong file type.** Only `audio/*` and `video/*` files are accepted; anything else is rejected
  with *expected an audio/\* file* (HTTP 415). An empty file is rejected with HTTP 422.
- **It just hasn't run yet.** Uploads are transcribed out-of-band by the scheduler, which polls the
  queue about **every 20 seconds**, and only **2 jobs** run at once by default
  (`MINUTES_UPLOAD_MAX_CONCURRENT`). Under a backlog, give it a moment. Status moves through the job
  as the worker picks it up; you can poll it in the UI or cancel it.

**Operator check** — see what the worker is doing:

```bash
docker compose logs -f scheduler
```

The scheduler runs `python -m app.jobs.transcription` on each tick; a transient failure is logged and
retried on the next tick.

---

## Health checks for the impatient (operator)

When something is off and you want a fast read on the stack:

```bash
DOMAIN=$(grep ^DOMAIN= .env | cut -d= -f2)
curl -fsS https://$DOMAIN/healthz      # is the process alive?
curl -fsS https://$DOMAIN/readyz       # are DB + Redis reachable?
docker compose ps                      # are all services up and healthy?
docker compose logs -f backend caddy   # the two logs that explain most problems
```

If `/readyz` is failing, the backend can't reach Postgres or Valkey/Redis — check those containers in
`docker compose ps`. If `/healthz` itself doesn't respond, the edge or the backend isn't up; check
Caddy's cert (see above) and the backend logs.

---

## Still stuck?

- Re-read the relevant page: [Deploy](/admin/deploy), [Managing users](/admin/users),
  [Uploads](/users/uploads), or the [Extension](/users/extension) guide.
- Capture the failing request and the matching line from `docker compose logs -f backend caddy`.
- minutes is open source — file an issue with those logs (with secrets redacted) and the exact symptom.
