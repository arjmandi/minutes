---
sidebar_position: 1
title: "Signing in & your settings"
---

# Signing in & your settings

Welcome to **minutes** — your self-hosted, EU-friendly tool for live meeting transcription and translation. This page gets you from zero to signed in, and walks you through your personal settings.

## What you need

Before you start, gather a few things:

- **A minutes account.** There's no public signup — your administrator creates your account for you and gives you an email and a temporary password.
- **Your server's URL.** This is the web address your team runs minutes on, something like `https://minutes.your-org.com`. Your admin will tell you this too.
- **The meeting (or tab) you want to capture.** minutes doesn't dial into calls — you join the Google Meet or Microsoft Teams meeting yourself (or just open any tab that plays audio — a webinar, a video, a podcast), and the [browser extension](/users/extension) captures that tab's audio.
- **Your own API keys:**
  - A **Soniox** key — get one at [soniox.com](https://soniox.com). Powers **transcription** — both live capture and [uploaded files](/users/uploads).
  - An **Anthropic** key — get one at [console.anthropic.com](https://console.anthropic.com). Needed for **translation**.

:::note Bring your own keys
Transcription uses **your** Soniox key, and translation uses **your** Anthropic key — set both under **Settings → API keys**. Your administrator *may* configure a shared Soniox key that live capture falls back to if you haven't added your own, but the recommended setup is your own key, with the **region** (US/EU) that matches it. More on this below.
:::

## Signing in

1. Open your server's app in a browser. The app lives at the `/app` path — for example, `https://minutes.your-org.com/app`. (On a plain self-hosted setup with no marketing landing page, the app may be at the root, `https://minutes.your-org.com/`.)
2. Enter the **email** and **password** your administrator gave you, and click **Sign in**.

That's it — you'll land on the main view, with your list of **Transcriptions** on the left and the transcript and translation columns to the right. (On a phone, the same app shows one screen at a time — see [On your phone](#on-your-phone) below.)

:::tip No account?
If you don't have an account yet, ask your administrator — there's no self-service signup. The sign-in screen says the same thing.
:::

## Change your password

Your admin set your first password, so change it to something only you know.

1. Click your email in the top-right corner, then choose **Settings**.
2. On the **Account** tab, find **Password**.
3. Enter your **current password**, then a **new password**, and click **Update password**.

Your new password must be at least **12 characters** and use at least **3 character classes** (for example, lowercase, uppercase, and digits). The form will tell you if it's too weak.

:::note
Changing your password signs out every other session — including the [browser extension](/users/extension) and any other browser — but keeps **you** signed in where you made the change. You'll need to sign in again from the extension afterward.
:::

## A tour of your settings

Open **Settings** from the menu under your email (top-right). There are four tabs: **Account**, **API keys**, **Translation**, and **Danger zone**.

### API keys

This tab is where you paste your own provider keys, stored encrypted — once saved, a key is never shown back to you (you'll see a "set" marker instead, and can replace it at any time).

- **Soniox API key** — powers **speech-to-text** for both your live captures and your [audio uploads](/users/uploads). Paste your key and click **Save**. (If your admin configured a shared server key, live capture falls back to it when you haven't set your own — but uploads always use yours.)
- **Soniox region** — **United States** or **European Union**. This is *data residency*: pick **EU** to have your audio processed on Soniox's EU endpoints. It must match the region of the Soniox project your key was created in — an EU key only works with **EU** selected. Your choice saves immediately and applies to both live capture and uploads.
- **Anthropic API key** — powers **translation** (live, on uploads, and the on-demand "translate this line" button). Without it, translation is simply off. Paste your key and click **Save**.

Here's the full picture of which key does what:

| Feature | Whose key is used |
| --- | --- |
| Live capture transcription | **Your** Soniox key + region (falls back to the server key, if your admin set one) |
| Audio-upload transcription | **Your** Soniox key + region |
| Translation (live, upload, on-demand) | **Your** Anthropic key |

:::tip Keep your data in your region
If you (or your customers) need audio to stay in the EU, create your Soniox key in an **EU project** and select **European Union** as your region. See [Configuration → Data residency](/admin/configuration#data-residency-soniox-region) for the operator's side.
:::

### Translation

These are your **defaults** — applied automatically when a new meeting starts. You can always override them for an individual meeting from the translation column's gear icon.

- **Translate new meetings by default** — a toggle. When on, translation starts automatically for each new meeting.
- **Default output language** — the language your transcripts get translated into. Choose from **English (en)**, **German (de)**, or **Persian (fa)**, or leave it blank for none.
- **Default model** — which Claude model translates: **Haiku** (fastest, cheapest), **Sonnet** (balanced), or **Opus** (best quality). Leave it on the default if you're unsure.

Click **Save defaults** when you're done. You can override the language and model per meeting from the translation column's gear icon (⚙).

:::note
Translation needs your **Anthropic key** (see the API keys tab). If you turn defaults on but haven't set a key, translation stays off until you add one.
:::

### Account

This tab shows your **email** (and an "admin" marker if you're an administrator) and is where you **change your password**, as described above.

### Danger zone

The last tab holds **Delete account** — a permanent, self-service way to remove your account. You'll be asked to type your email to confirm. Meetings you own become unowned (an admin can still see them), and the action can't be undone.

## Two audio sources in one meeting

A single meeting can capture **two independent audio sources** at once, kept fully separate — each keeps its own transcript, its own translations, and its own timestamps:

- **Online stream** — the audio coming from the browser tab you're capturing (the Meet/Teams call, a webinar, a video). Marked in **orange**.
- **Host mic** — your own microphone, the person doing the capturing. Marked in **blue**.

(A third, single-source kind, **Upload**, is what you get from an [uploaded file](/users/uploads) — it never has a second source.)

When a meeting has **both** sources, a small **segmented control** appears in the meeting header — **Online stream | Host mic**, each with its colored mark. Click either one to switch which source you're reading: the **transcript and its translation switch together, instantly**, with no reload. A meeting that only ever had one source shows **no switcher** — there's nothing to choose between.

:::note
The two sources stay separate — you read one at a time. A combined, side-by-side view of both at once isn't available yet; it's a planned addition.
:::

:::tip
Switching sources only changes what you're looking at — both sources keep recording and translating in the background. Switch back and the other source's lines are right where you left them.
:::

## On your phone

minutes works in a mobile browser too. On a small screen the three columns collapse into one view at a time:

- Your **Transcriptions** list is the home screen — tap a row to open it, or the **⟳** to refresh.
- A meeting opens full-screen with a **Transcript / Translation** toggle at the top; tap **‹** to return to the list.
- When a meeting has **both** audio sources, a row of **source chips** (**Online stream** / **Host mic**, each with its colored mark) sits just above that Transcript / Translation toggle — tap a chip to switch which source you're reading, the same as the desktop switcher. Single-source meetings show no chips.
- The account menu, a meeting's actions (rename, export, share, delete), and the per-meeting translation settings each slide up as a **bottom sheet**.

The [capture extension](/users/extension) is desktop-Chrome only, so you start recordings from a computer — but you can read, translate, share, and export from your phone.

## Where to next

- [Set up the browser extension](/users/extension) — install it, point it at your server, and start capturing meetings.
- [Meetings & export](/users/meetings-and-export) — rename meetings, translate lines, share, and export transcripts.
- [Uploads](/users/uploads) — transcribe an existing audio or video file (this is where your Soniox key comes in).
