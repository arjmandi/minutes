---
sidebar_position: 5
title: "Uploading audio files"
---

# Uploading audio files

Got a recording instead of a live meeting? You can upload an audio (or video) file and minutes will transcribe it just like a meeting — with the same transcript view, translation, and export options.

## How to upload

1. Open the web app.
2. Click **Upload** in the top navigation bar.
3. Pick a file from your computer.

That's it. minutes creates a new meeting for the recording (named after your file) and starts transcribing it in the background. You don't have to keep the page open — come back any time to check on it.

## What you need first: your Soniox key

Uploads are transcribed with **your own Soniox key** (there's no server fallback for uploads). So before you upload, open **Settings → API keys** and add your Soniox API key (get one at [soniox.com](https://soniox.com)). Your **Soniox region** (US/EU) there applies to uploads too — the file is processed in the region you chose.

:::warning Without your key, the job fails
If you haven't set your own Soniox key, the upload is accepted but the transcription job ends with a **failed** status and the message `no Soniox API key configured for the owner`. Add your key in Settings, then upload again.
:::

Live capture uses the **same** key (with an optional server-key fallback your admin may configure); uploads always require your own. See [Signing in & your settings](/users/web-app) for the full picture of which key does what.

## Supported file types

minutes accepts common audio and video uploads. Soniox decodes the audio for you, so you can upload any of these:

- **Audio:** WAV, MP3, M4A / AAC, FLAC, OGG / Opus, WebM
- **Video:** MP4, MOV — only the audio track is transcribed

The app accepts files whose type starts with `audio/` or `video/`. Anything else (a PDF, an image, a zip) is rejected before upload with an "expected an audio/* file" error.

## Limits

| Limit | Value | What it means |
| --- | --- | --- |
| File size | ~300 MB per file | Larger files are rejected (`file too large`). Set by `MINUTES_UPLOAD_MAX_BYTES`, default `314572800` bytes. |
| Concurrency | 2 jobs at once | At most two uploads are transcribed at the same time across the whole server. Set by `MINUTES_UPLOAD_MAX_CONCURRENT`. |

:::note Your operator can change these
Both limits are server settings. If you're hosting minutes yourself (or your admin is), the size cap and how many jobs run together can be adjusted in the deployment's `.env`.
:::

## How processing works

Uploads are transcribed **out of band** — a background scheduler worker picks up queued jobs on a short interval (about every 20 seconds), so a freshly uploaded file usually starts within a few seconds and the app stays responsive.

Each job moves through these statuses:

- **queued** — accepted and waiting for a free worker slot.
- **processing** — being transcribed (and translated, if enabled).
- **done** — finished; open the meeting to read the transcript.
- **failed** — something went wrong (for example, no Soniox key, or an unreadable file). The error is shown alongside the job.
- **canceled** — you stopped it.

You can **cancel** a queued or in-progress upload. If the cancel lands while transcription is mid-flight, it still wins — the job won't quietly finish behind your back.

:::tip Larger files take longer
A long recording is one big job, so it can take a while. The status will sit at **processing** until Soniox returns the full transcript. Big files near the 300 MB cap are the slowest.
:::

## Translation

Translation of an uploaded recording follows the **same defaults as your meetings**. When the upload's meeting is created, its translation settings are seeded from your account defaults (whether translation is on, the output language, and the model). If translation is enabled, each transcribed line is translated using **your own Anthropic key** — exactly like a live meeting.

No Anthropic key, or translation switched off? You still get the transcript; translation is simply skipped. You can also turn translation on (or change the language) on the meeting afterward.

## After it's done

Once a job reaches **done**, the upload is just a regular meeting. You can rename it, read and translate the transcript, and export it.

See [Meetings and export](/users/meetings-and-export) for everything you can do with the finished transcript.
