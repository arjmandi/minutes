---
sidebar_position: 2
title: "The capture extension"
---

# The capture extension

The minutes capture extension is a small Chrome extension that streams the audio from **a browser tab** to your minutes server, where it's turned into a live transcript (and, if your account has a translation key, a live translation).

It's tuned for **Google Meet** and **Microsoft Teams** calls — those get the real meeting name/ID automatically — but because it captures the tab's **audio output**, it works on **any tab that plays audio**: a webinar, a recorded video or lecture, a podcast, an earnings call, or another meeting tool. On a non-Meet/Teams tab it captures as a generic recording named after the tab's title.

A capture can also include a second, independent source — **your own microphone** — so your side of a call is transcribed too. The two sources stay completely separate (separate transcripts, translations, and timestamps); see [Capture your own voice](#capture-your-own-voice-host-mic).

By default it captures only the audio playing in that one browser tab — your microphone, your camera, and your other tabs are untouched, and you keep hearing the audio normally. Your microphone is added only if you turn it on.

:::note It captures what the tab plays
By default the extension records the tab's **output** (what you hear), not your microphone. For a video, podcast, or webinar that's everything; for a live call it's the **other** participants (the same as any tab-based capture). To also transcribe *your* voice, turn on the **Host mic** (below). DRM-protected streams and `chrome://` pages can't be captured.
:::

:::note Who this is for
You sign in with the account your administrator created for you. There's no public signup. If you don't have credentials yet, ask whoever runs your minutes server to create your user.
:::

## Install the extension

The extension isn't on the Chrome Web Store yet, so you load it "unpacked" from the project files. It's a one-time setup.

1. Open Chrome and go to `chrome://extensions`.
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked**.
4. Select the `capture/extension` folder from the minutes project.

The **minutes capture** icon now appears in your toolbar. (Pin it from the puzzle-piece menu if you don't see it.)

:::tip Web Store packaging is coming
A packaged Web Store version is planned. For now, loading unpacked is the supported way to install — and it stays installed across restarts.
:::

## First run: point it at your server

The extension doesn't ship with a server address — you tell it where your minutes instance lives. The **gear** icon opens the extension's single Settings page, which holds the Server URL, the [Host-mic setup](#capture-your-own-voice-host-mic), and the [silence-suspend](#silence-suspend-saves-on-quiet-stretches) toggle. You can open it anytime, not just on first run.

1. Click the minutes icon to open the popup. On first run it shows **"Set your server to begin."**
2. Click **Open settings →** (or the **gear** icon) to open the Settings page.
3. In **Server URL**, enter the full address of your minutes instance, for example `https://minutes.your-company.com`.
4. Click **Test connection**. You should see a green **Connected** chip. (At this point it'll say "Reachable, but you're not signed in" — that's expected; you'll sign in next.)
5. Click **Save**.

:::warning Your server needs a real domain (https://)
The extension streams audio over a **secure WebSocket** (`wss://`), which requires HTTPS. That means your server URL has to be a real domain with TLS — `https://minutes.your-company.com`, not a bare IP address. A plain `http://localhost` address works only for local development. If you're not sure what your server URL is, ask your administrator.
:::

The **Test connection** check probes your server's health endpoint. If it says it can't reach the URL, double-check the address (including `https://`) and that the server is up.

## Sign in

Once a Server URL is saved, the popup shows a sign-in form.

1. Click the minutes icon to open the popup.
2. Enter the **email** and **password** for the account your administrator created.
3. Click **Sign in**.

:::tip Your password is never stored
Signing in exchanges your email and password for a **device token** that stays on this browser. Your password itself is never saved by the extension. The device token is what keeps you signed in between meetings.
:::

## Record a meeting

With a server set and your account signed in, you're ready to capture.

1. Open what you want to capture in a Chrome tab — a **Google Meet** call (`meet.google.com`), a **Microsoft Teams** meeting (`teams.microsoft.com` or the new `teams.cloud.microsoft`), or **any other tab playing audio** (a webinar, a video, a podcast). For Meet/Teams, join with your own account as usual.
2. Click the minutes icon. The popup **auto-detects the tab**: Meet/Teams show the platform + meeting ID; any other tab shows its title (e.g. the video name) as a generic capture. This tab's audio is the **Online stream** source.
3. *(Optional)* Toggle **Also capture my microphone** to add the **Host mic** as a second source — see [Capture your own voice](#capture-your-own-voice-host-mic).
4. Click **● Start recording**.

:::note Meeting ID vs. generic captures
Classic Meet/Teams carry a meeting ID in the URL, so reconnecting to the same call appends to the same record. The new Teams web app and other sites don't expose one — the popup shows **"current call"** or the tab title, and each capture gets its own record. Either way, make sure audio is actually playing before you press **Start recording**.
:::

The toolbar icon switches to the **recording mark** (with a red dot), and the popup shows a **RECORDING** indicator, the server it's streaming to, and a **chip for each live source** (Online stream and, if enabled, Host mic) with a running frame count. With both sources on, the header reads **RECORDING · 2 sources** and the button becomes **■ Stop both**.

When you're done, click the minutes icon and press **■ Stop** (or **■ Stop both**). The icon returns to its idle state.

:::note You still hear everything
The extension captures the tab's audio for transcription, but it doesn't mute it or take it over — the meeting plays normally for you the whole time. The Host mic, if enabled, is captured silently — it's never played back, so it can't echo into the tab.
:::

## Capture your own voice (Host mic)

A capture has two possible sources, kept fully separate — separate transcripts, translations, and timestamps:

- **Online stream** — the browser tab's audio (the call, a video, a podcast). This is the original capture, and it's always on.
- **Host mic** — your own microphone. **Off by default.** Turn it on with **Also capture my microphone** in the popup (or in Settings).

Why a second source? In a Meet or Teams call your own voice isn't played back into the tab, so capturing the tab alone misses *you* — it only hears the other participants. The Host mic adds your side on its **own track**. There's no second bot account and no extra participant in the meeting; the microphone is captured locally in your browser, exactly like the tab audio.

:::tip One-time setup, then a single toggle
The first time you enable the mic, Chrome asks for a one-time microphone-permission grant on the [Settings page](#mic-setup-lives-in-settings). After that, **Also capture my microphone** in the popup is all you touch — and when the mic is ready, the popup shows **Mic ready** with your selected device, and **Start recording** becomes **● Start recording · 2 sources**.
:::

:::note Two sources mean two transcription connections
Tab and mic open two separate Soniox connections under your key. If your Soniox plan caps concurrency, the second connection may be rejected — minutes shows an error chip for that source while the other keeps recording. The two transcripts are stored separately and merged in the web app.
:::

### Mic-only capture (a tab with no audio)

You don't need a call to use the mic. On a tab with **no capturable audio** — a blank or new tab, or a non-`http` page like `chrome://` — with the mic enabled, the popup offers to capture **just your microphone**: the button reads **● Start recording (mic only)**. It creates a meeting titled **"Mic recording"**.

Use it for dictation, a voice note, or transcribing an in-person talk where there's nothing playing in the browser.

## Silence-suspend (saves on quiet stretches)

The Host mic has a cost-saving feature, **on by default**: during sustained silence it stops streaming to Soniox (so you're billed for fewer seconds) and resumes the moment you speak again — with a short pre-roll so the start of your first word isn't clipped. It only applies to the Host mic; the Online stream is always streamed in full.

Your segment timestamps still track **real meeting time** — the dropped silence is added back on the server, so nothing shifts earlier.

Toggle it in [Settings](#mic-setup-lives-in-settings). Leave it on; only turn it off if you find **quiet speech getting dropped** (for example, a very soft speaker).

## Mic setup lives in Settings

Everything for the microphone lives on the extension's **Settings page** (the **gear** icon), alongside the Server URL — and it's available **anytime**, not just before a recording. There's no separate permission page. The page lets you:

- **Grant microphone access** — the one-time Chrome permission prompt.
- **Pick an input device** — choose which microphone to use.
- **Test the level** — a live meter; speak and watch it move to confirm you picked the right mic.
- **Echo cancellation** — **off by default.** With the tab playing on your speakers, the browser's echo-canceller uses the tab audio as its reference and can clip your speech; on headphones you can leave it off too. Turn it on only if you hear the tab bleeding into your mic track.
- **Silence-suspend** — the cost saver described above.

The popup's **Grant microphone access** and **Test / change** links both open this page.

:::tip Adjust anytime
Because Settings is always available, you can change your mic device, echo cancellation, or silence-suspend whenever you like. They apply to your **next** recording — stop the current capture, adjust, then start again.
:::

### What the popup states mean

Depending on where you are in setup, the popup shows one of these:

| State | What it looks like | What to do |
| --- | --- | --- |
| **No server** | "Set your server to begin" | Open Settings and enter your Server URL. |
| **Signed out** | Email + password form | Sign in with your account. |
| **Nothing to capture** | "Nothing to capture here" (no tab audio, mic off) | Open a tab playing audio — or turn on the mic to record [mic-only](#mic-only-capture-a-tab-with-no-audio). |
| **Idle** | Source detected, **Start recording** enabled, plus the **Also capture my microphone** toggle | Optionally enable the mic, then click **Start recording**. |
| **Recording** | **RECORDING** badge and a live chip per source (with frame counts) | Capture is live; click **Stop** (or **Stop both**) to finish. |

The toolbar icon itself is a quick indicator of which sources are live: **idle** when nothing is being captured, and a distinct mark for **Online stream** only, **Host mic** only, or **both**.

### Sign out

To sign out, open the popup and click **Sign out** at the bottom. This stops any active capture and removes the device token from this browser. You'll need to sign in again next time.

## Watch the transcript

Once recording starts, the transcript appears **live in the minutes web app** — you don't watch it in the extension. Open your meeting in the web app to follow the words as they arrive, rename the meeting, turn on translation, and export when you're done.

See [Meetings and export](/users/meetings-and-export) for everything you can do with a transcript.

## Troubleshooting

- **"Nothing to capture here"** — The active tab has no capturable audio (a blank/new tab, or a `chrome://` page). For a call, make sure you're on `meet.google.com`, `teams.microsoft.com`, or `teams.cloud.microsoft`; on the new Teams web app there's no ID in the URL, so the popup shows "current call" — that's expected, and capture still works once you're in the call. If you only want your own voice, turn on the mic and record [mic-only](#mic-only-capture-a-tab-with-no-audio).
- **Sign-in fails with "Invalid email or password"** — Confirm your credentials with your administrator; there's no self-service password reset in the extension.
- **Test connection fails** — Recheck the Server URL (full `https://` address) and that the server is reachable. Remember a bare IP won't work for capture.
- **Recording won't start / authorization error** — Your session may have expired; the extension will return you to the sign-in screen. Sign in again and retry.
- **Mic permission is blocked** — If you previously denied the prompt, open the [Settings page](#mic-setup-lives-in-settings); it walks you through re-enabling minutes under `chrome://settings/content/microphone`, then re-checks.
- **The Host mic chip shows an error while the call keeps recording** — The mic's Soniox connection was rejected (often a plan concurrency cap, since tab + mic open two connections). The Online stream keeps going; check your Soniox plan if you need both at once.
- **Your own voice is missing from the transcript** — The Host mic is **off by default**. Turn on **Also capture my microphone** before you start. If quiet speech is getting dropped, turn off [silence-suspend](#silence-suspend-saves-on-quiet-stretches) in Settings.
