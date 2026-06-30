---
sidebar_position: 3
title: "Getting started"
---

# Getting started

**minutes** is a self-hosted, EU-friendly tool for live transcription and translation. It captures the audio of any browser tab — a Google Meet or Microsoft Teams call, a webinar, a video — transcribes it as it plays (English, German, Persian), and — if you give it an Anthropic key — translates each line into a language you choose. Everything runs on infrastructure your organization controls.

There are three pieces:

- A **web app** — the product UI where transcripts appear live, get translated, exported, and shared.
- A **Chrome capture extension** — streams the audio of your Meet/Teams browser tab to the backend.
- A **FastAPI backend** — the single-box Docker stack (Caddy TLS edge, backend, Postgres, Valkey/Redis, MinIO object store, and a scheduler) that runs the pipeline and stores everything.

The pipeline in one line: the extension captures tab audio → [Soniox](https://soniox.com) turns it into text → [Anthropic / Claude](https://console.anthropic.com) optionally translates it → the live transcript shows up in the web app. Only those two external services ever receive your data; everything else stays on the operator's box.

:::tip Which track are you?
This page splits into two short tracks. Pick the one that matches you — you do not need to read both.

- **Administrators** stand up the server and create accounts. → [Track A](#track-a-administrators)
- **Users** sign in, set their keys, install the extension, and capture meetings. → [Track B](#track-b-users)
:::

## Who needs what

A quick map of who touches which part:

```text
  Administrator                              User
  ─────────────                              ────
  • A domain + an EU VPS                      • An account (the admin makes it)
  • Docker + the minutes repo                 • Their OWN Soniox key + region (live + uploads)
  • Creates user accounts via CLI             • Their OWN Anthropic key (for translation)
  • (optional) a fallback Soniox key          • The Chrome capture extension + a tab to capture
        │                                              │
        └──────────────►  one minutes server  ◄────────┘
                          (web app + backend)
```

The single shared resource is the **server**. The admin runs it (and may set an optional fallback Soniox key); each user brings their own keys — Soniox for transcription, Anthropic for translation — for the features that bill to them.

---

## Track A: Administrators {#track-a-administrators}

You are technical and you run the box. Three things get you from zero to a working server.

### 1. Deploy the server

minutes ships as a one-VPS Docker Compose stack. The short version:

```bash
git clone <your-fork-of-minutes> && cd minutes/deploy/single-box
cp .env.example .env
# fill in .env (DOMAIN, MINUTES_SONIOX_API_KEY, passwords, the two secrets)
docker compose up -d --build
```

A one-shot `migrate` service runs `alembic upgrade head` before the backend starts. To update later: `git pull && docker compose up -d --build`. Caddy auto-provisions a Let's Encrypt TLS certificate for your `$DOMAIN`, so inbound TCP **80 and 443 must be public**.

:::warning A real domain is required
The capture extension opens a **secure** WebSocket (`wss://`) from the browser's secure context. That needs TLS, which (via Caddy + Let's Encrypt) needs a real public **domain** with an `A` record pointing at the VPS. A bare IP or `http://localhost` only works for local development (`ws://`, no TLS) — the extension and the HTTPS web app will not work without a domain.
:::

The full walkthrough — sizing, prerequisites, boot order, verifying, operating, and backups — is on the deploy page.

→ **[Deploy a server](/admin/deploy)**

### 2. Create user accounts

There is **no public signup**. You create every account from the CLI inside the backend container:

```bash
docker compose exec backend python -m app.admin create-user --email you@org.com [--admin]
```

The **first user you create is automatically an admin**. Other commands: `set-password --email`, `delete-user --email`, and `list-users`.

→ **[Manage users](/admin/users)**

### 3. Hand out the basics

Tell each new user their server URL and that you've created their account, then point them at [Track B](#track-b-users). They will set their own password, plug in their own keys, and install the extension.

:::note What the server pays for vs. what users pay for
Both keys are **per-user**: each user sets their own **Soniox** key (transcribes their live captures *and* uploads) and their own **Anthropic** key (translation) in the web app. The optional `MINUTES_SONIOX_API_KEY` in `.env` is only a **fallback** for live capture when a user hasn't added their own — handy for a quick demo, but leave it empty to require per-user keys. See [Configuration → keys](/admin/configuration#stt-and-translation-keys).
:::

---

## Track B: Users {#track-b-users}

You are here to capture meetings. No server setup — your administrator has already done that and given you a server URL and an account.

### 1. Sign in

Open the web app your admin gave you (typically `https://your-domain/app`) and sign in with your email and password. This is also where everything you capture will appear, live.

→ **[Using the web app](/users/web-app)**

### 2. Set your keys

In the web app's **Settings**, add your own API keys. These unlock the features that bill to you:

- **Soniox key** ([soniox.com](https://soniox.com)) — powers **transcription**, both your live captures and **audio files you upload**. Also pick your **Soniox region** (US/EU) next to the key — that's where your audio is processed.
- **Anthropic key** ([console.anthropic.com](https://console.anthropic.com)) — needed for **translation** (live, on uploads, and the on-demand "translate this line"). No key simply means translation is off.

:::note
Transcription uses **your** Soniox key. If your admin set a shared fallback key, live capture still works before you add your own — but uploads always need yours, and your own key lets you choose your data region.
:::

### 3. Install the capture extension

Load the Chrome capture extension, open its settings (the gear icon in the popup), and enter your **Server URL**. The same settings page is also where you set up the optional **Host mic** (capture your own voice as a second track). The popup signs you in with your email and password, and from then on mints a short-lived token for each tab it captures. The toolbar icon shows whether it's idle or recording — and which sources are live.

→ **[Install the extension](/users/extension)**

### 4. Capture a meeting

1. Join your Google Meet or Microsoft Teams meeting as you normally would, in Chrome, with your own Google/Microsoft account. (minutes captures that tab's audio — you have to actually be in the call.)
2. Open the extension popup on the meeting tab and start capture.
3. *(Optional)* Toggle **Also capture my microphone** to add your own voice as a second, separate track (**Host mic**). In Meet/Teams your own voice isn't played back into the tab, so this is how you get *your* side transcribed — no second bot account joins the call. It's off by default and needs a one-time mic-permission grant.
4. Watch the live transcript — and, if you set an Anthropic key, the translation — appear in the web app.

From there you can translate individual lines on demand, rename the meeting, export it (`txt` / `md` / `json`, transcript / translation / both, with or without timestamps), and create read-only public share links.

:::tip No call? Record just your microphone
On a blank tab (or any page with no audio), turn on the mic and the popup offers **mic-only** capture — the button reads **Start recording (mic only)**. Handy for dictation, voice notes, or an in-person talk. See [the extension guide](/users/extension#capture-your-own-voice-host-mic) for the full Host-mic setup.
:::

→ **[Using the web app](/users/web-app)** for the full feature tour.

:::tip
Have a recording instead of a live call? Upload the audio or video file in the web app — minutes transcribes it out-of-band using your Soniox key. Uploads are capped at roughly 300 MB each, with at most two processing at once.
:::
