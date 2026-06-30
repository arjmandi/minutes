---
sidebar_position: 3
title: "Record in the app"
---

# Record in the app

You don't need the browser extension — or a second device — to capture your own voice. **minutes can record your microphone directly in the web app**, on a computer or a phone, and turn it into a normal meeting: the same live transcript, translation, export, and sharing as everything else.

This is the third way to capture in minutes, alongside the [Chrome extension](/users/extension) (a browser tab's audio) and [uploading a file](/users/uploads). All three produce the same kind of meeting.

:::note It's your microphone only (Host mic)
In-app recording is a **single-source, mic-only** capture — it records the **Host mic**, your own voice. It can't capture a browser tab's audio or an online call; for that, use the [Chrome extension](/users/extension), which captures the **Online stream** (and can add your mic as a second track). In the app, recording is just *you* and your microphone.
:::

## Where to find it

- **On a computer**, open the web app. In the **Transcriptions** list header, on the left, there's a **● Record** button right next to **↑ Upload**.
- **On a phone**, the **Transcriptions** screen shows a prominent **● Record** action at the top (with **Upload audio file** just below it).

Either way, **Record** is the in-app microphone recorder. **Upload** is for [files you already have](/users/uploads).

:::warning Recording needs a secure context (HTTPS)
Capturing your microphone in the browser requires a **secure context** — your deployed site over **HTTPS** (`https://minutes.your-org.com`). It won't work over plain `http://` (other than `http://localhost` for local development). If your admin set up a real domain with TLS, you're good. See [Getting started → Deploy](/getting-started#track-a-administrators) for the operator's side.
:::

## The recording flow

Recording walks through a few quick steps.

### 1. Tap Record and grant the microphone

The first time, your browser asks for **microphone permission**. Allow it. (The recorder captures your mic silently — it's never played back, so it can't echo.)

If the mic is **blocked** — you denied it earlier, or your browser has it off for this site — minutes shows a short prompt to re-enable it rather than failing silently. On iOS that's **Settings → minutes → Microphone**; in a desktop browser, open the site's permission settings, turn the microphone on, then reopen the recorder. (You can always [upload an audio file](/users/uploads) instead.)

### 2. Set up the recording

Before recording starts, a short setup step lets you check your input and pick a couple of options:

- **Level test** — a live meter. Speak and watch it move; it reads **picking up audio** once it hears you. This confirms your browser is using the microphone you expect before you commit.
- **Name** *(optional)* — give the recording a title up front (for example, *Standup note*). Leave it blank and it's named after the date and time; you can [rename it](/users/meetings-and-export#rename-a-meeting) later anyway.
- **Echo cancellation** — **off by default.** The browser's echo-canceller can clip your voice, especially on headphones. Leave it off unless you're recording out loud near speakers and hear problems; then turn it on.
- **Silence-suspend** — **on by default.** During sustained silence the recorder stops streaming to Soniox (so you're billed for fewer seconds) and resumes the instant you speak again, with a short pre-roll so your first word isn't clipped. Your timestamps still track real elapsed time — the dropped silence is added back on the server, so nothing shifts. Leave it on; only turn it off if you find very quiet speech getting dropped. (This is the same feature the [extension's Host mic](/users/extension#silence-suspend-saves-on-quiet-stretches) uses.)

### 3. Record

Press **● Record** and capture begins. You get a live view with:

- An **elapsed timer**.
- A **level meter** so you can see it's still hearing you.
- A **Stop** button.
- On a phone, a **"Screen stays on while recording"** note — minutes holds a **screen wake lock** so your phone doesn't sleep mid-recording. (If you switch away and come back, the lock re-acquires.)
- The **live transcript**, streaming in as you speak — and the translation too, if you have translation on.

:::tip Keep the page open while recording
Recording runs in the page, so keep the app open (and the screen awake) for the length of the recording. On a phone the wake lock handles the screen for you; just don't close the tab or the app.
:::

### 4. Stop — and you have a meeting

Press **Stop**. The recording is finalized into an ordinary meeting:

- On a computer, you're dropped straight into it in the three-column view.
- On a phone, you get a **Recording saved** screen with **Open transcript →** and **Record another**.

From there it's a normal meeting — [read and translate it](/users/meetings-and-export), [copy the text](/users/web-app#copy-the-transcript-or-translation), [export it](/users/meetings-and-export#export) as `txt` / `md` / `json`, and [share a read-only link](/users/sharing) — exactly like a captured call or an upload.

:::note You still need your keys
Recording transcribes with **your own Soniox key** (region included), and translates with **your own Anthropic key** — the same keys as everywhere else, set under **Settings → API keys**. See [Signing in & your settings](/users/web-app#api-keys) for which key does what.
:::

## Install minutes on your phone (PWA)

The web app is an installable **Progressive Web App (PWA)** — add it to your home screen and it opens **full-screen, like a native app**, which makes "record on your phone" feel first-class. It still runs entirely against your own minutes server.

### iPhone / iPad (Safari)

Safari has no automatic install button, so do it from the Share menu:

1. Open the app in **Safari** (your `https://` server URL).
2. Tap the **Share** icon.
3. Choose **Add to Home Screen**, then **Add**.

minutes shows a one-time hint reminding you of these steps; you can dismiss it. After that, launch minutes from its home-screen icon and it runs full-screen.

### Android (Chrome)

Chrome offers a built-in prompt:

1. Open the app in **Chrome** (your `https://` server URL).
2. Accept the **Install** prompt when it appears (or use the browser menu's **Install app** / **Add to Home screen**).

The installed app launches full-screen from your home screen.

:::warning Install from the HTTPS site
Installing — and recording — needs the **secure (HTTPS) site**. The PWA won't install over plain `http://`. If you're testing against `http://localhost` in development, install and capture won't be offered. Use your real deployed domain.
:::

## On a computer vs. a phone

Recording works in both desktop and mobile browsers over HTTPS — the steps and options are identical (level test, name, echo cancellation, silence-suspend). The presentation differs:

- **Desktop** — Record sits in the Transcriptions list header. The live recording shows as a small floating bar (timer + **Stop**) while you keep using the app; the transcript fills in the main column.
- **Phone** — Record is a full-screen flow (setup → live → saved), with the on-screen timer, meter, live transcript, and the screen-wake-lock note. Installing minutes as a [PWA](#install-minutes-on-your-phone-pwa) makes this the smoothest.

## Related

- **[The capture extension](/users/extension)** — capture a browser tab's audio (a Meet/Teams call, a video) on desktop Chrome, optionally with your mic as a second source.
- **[Uploading audio files](/users/uploads)** — already have a recording? Drop in the file instead.
- **[Meetings, translation & export](/users/meetings-and-export)** — everything you can do with the transcript once you've stopped.
