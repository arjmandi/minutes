---
sidebar_position: 1
title: "What is minutes?"
slug: /
---

# What is minutes?

**minutes** is a self-hosted tool for **live transcription and translation**. Join a Google Meet or
Microsoft Teams call — or just open a webinar, a recorded video, or a podcast — and minutes turns the
audio into a written transcript as it plays, and (if you want) translates each line into another
language in real time. You can also **record your own microphone right in the app** (on a computer or
a phone, no extension) or **upload a recording you already have**. The whole transcript streams into a
clean web app you can read, search, rename, copy, share, and export.

It's built for teams who want this capability **on infrastructure they run**, not handed to a
third-party meeting-bot service. minutes is multi-user, EU-friendly, and designed with privacy in
mind.

:::tip Who is this for?
Two audiences. **Operators / admins** who deploy and run minutes for their organization, and **end
users** who join meetings and read the live transcripts. The docs are split the same way — jump to
[Where to go next](#where-to-go-next) below.
:::

## The three parts

minutes is made of three pieces that work together:

1. **The web app** — the product UI. This is where you see live transcripts, turn translation on,
   rename meetings, copy, export, and create share links. It can also **record your own microphone**
   directly (no extension), and it **installs to your phone's home screen** as a full-screen app. It
   runs at `/` (and also `/app`) on your server.
2. **The Chrome capture extension** — a small browser extension (loaded from `capture/extension`)
   that captures the audio of **any browser tab** and streams it to your backend. Meet and Teams are
   auto-detected (and named with the real meeting ID); any other tab playing audio works too. You
   sign in through the extension's popup and start capturing the tab you're on.
3. **The backend** — a [FastAPI](https://fastapi.tiangolo.com) service that runs the pipeline and
   stores everything. It ships as a single-box Docker Compose stack: a **Caddy** TLS edge, the
   **backend** itself, **Postgres** (durable transcripts and translations), **Valkey/Redis** (live
   fan-out and admission control), a **MinIO** object store (archived audio), and a **scheduler** for
   background work like upload transcription and retention purges.

## Three ways to capture

However you capture, you get the **same kind of meeting** — the same live transcript, translation,
copy, export, and sharing:

1. **A browser tab's audio** — the [Chrome extension](/users/extension) streams the audio of any tab
   (a Meet/Teams call, a webinar, a video) from your desktop browser. It can also add **your own
   microphone** as a separate track at the same time.
2. **Your microphone, in the app** — [record directly in the web app](/users/recording), on a
   computer or a phone, with **no extension and no second device**. It's a mic-only capture (the
   **Host mic** source).
3. **An audio file** — [upload a recording](/users/uploads) you already have and minutes transcribes
   it in the background.

## How it works: the pipeline

The path from a spoken sentence to a translated line on your screen is short and only ever touches
two outside services:

```text
Audio in  (a browser tab, your microphone, or an uploaded file)
        │  (the extension, in-app recording, or an upload streams it in)
        ▼
   minutes backend  ──►  Soniox  (speech-to-text: en / de / fa)
        │                         returns the transcribed text
        ▼
   minutes backend  ──►  Anthropic / Claude  (optional translation)
        │                                      returns the translated text
        ▼
   Live transcript in the web app  (searchable, exportable, shareable)
```

1. **Capture** — audio reaches the backend over a secure WebSocket, whether from the extension (a
   tab), in-app recording (your mic), or an upload.
2. **Speech-to-text** — the backend sends audio to **Soniox**, which returns the words.
3. **Translate** — each finalized line is optionally sent to **Anthropic (Claude)** and translated
   into the meeting's chosen output language.
4. **View** — the transcript (and translation) streams live into the web app.

:::note Only two services ever see your data
**Soniox** receives audio (for transcription) and **Anthropic** receives text (for translation).
**Everything else stays on the box you operate** — Postgres, Redis, object storage, and the app
itself. For GDPR, host the box in an **EU region** and select Soniox's **EU** data-residency region
so the audio is processed in the EU too (see
[Configuration → Data residency](/admin/configuration#data-residency-soniox-region)).
:::

## Supported languages

minutes recognizes and works with three languages:

| Language | Code | Notes |
|---|---|---|
| English | `en` | |
| German  | `de` | |
| Persian | `fa` | Right-to-left (RTL); rendered correctly throughout |

Speech is transcribed in these languages, and translation can target one output language per meeting.

## What you can do with it

- **Live transcription** of any tab you capture — a Meet/Teams call, a webinar, a video — as it happens.
- **In-app recording** — [record your own microphone right in the web app](/users/recording), on a
  computer or a phone, with **no extension and no second device**. It's a mic-only **Host mic**
  capture that streams to the same pipeline and becomes a normal meeting. Great for dictation, a voice
  note, or an in-person talk.
- **Dual-source capture** — with the extension, record the tab's audio (**Online stream**) and your
  own microphone (**Host mic**) at once, kept fully separate (their own transcripts and timestamps).
  In Meet/Teams your own voice isn't played into the tab, so the Host mic adds your side — no second
  bot account.
- **Translation** — one output language per meeting, with an optional custom prompt and model, plus
  on-demand "translate this line" for a single line.
- **Manage meetings** — rename them and read absolute timestamps for every line.
- **Copy** the whole transcript (or translation) for the source you're viewing, as plain text with no
  timestamps — a one-click clipboard grab for pasting elsewhere.
- **Export** transcripts as `txt`, `md`, or `json`; choose transcript, translation, or both, with or
  without timestamps.
- **Audio upload** — transcribe an existing recording (audio or video container) instead of a live
  call. Processed in the background by the scheduler.
- **Public share links** — share a read-only view via an opaque token you can rotate or disable.
- **Install on your phone** — the web app is an installable **PWA** that runs full-screen from your
  home screen, and it's fully responsive, so you can **record**, read, translate, copy, share, and
  export from a phone. (The capture extension itself runs on desktop Chrome.)
- **GDPR controls** — an optional consent gate, a configurable retention purge, and an erasure path
  for owners and admins.

:::warning Accounts are admin-created
There is **no public signup**. An admin creates each user from the command line, and users set their
own API keys in the web app's Settings. See [Getting started](/getting-started) for what a user
needs, and [Deploy](/admin/deploy) for the admin side.
:::

## Privacy and self-hosting at a glance

- **You run it.** A single VPS with Docker runs the whole stack.
- **Two external processors only:** Soniox (audio) and Anthropic (text). No third-party meeting bot
  joins your calls — capture happens in *your* browser.
- **EU-friendly.** Built with GDPR in mind: consent gate, retention limits, and data erasure — plus
  **EU data residency** end to end when you pick Soniox's EU region.

## Where to go next

Pick the path that fits you:

- **Admins / operators** → head to [Deploy](/admin/deploy) to stand up the single-box stack and
  create your first users.
- **End users** → head to [Getting started](/getting-started) to set up your keys and start your
  first live transcript — capture a tab with the [extension](/users/extension), or just
  [record your microphone in the app](/users/recording).
