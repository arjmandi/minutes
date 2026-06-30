---
sidebar_position: 4
title: "Privacy policy (extension)"
slug: /privacy
---

# Privacy policy — minutes capture extension

_Last updated: 2026. This policy covers the **minutes capture** Chrome extension. Use this page's
URL (e.g. `https://gettheminutes.com/docs/privacy`) as the privacy policy link in your Chrome Web
Store listing._

## What the extension does

The minutes capture extension streams the **audio of the Google Meet or Microsoft Teams tab you
choose** to a **minutes server that you (or your administrator) configure** — and nowhere else. It
exists to turn your meeting audio into a live transcript and translation on that server.

## What data it handles

- **Meeting tab audio.** While you are recording, the audio playing in the active meeting tab is
  captured and streamed to your configured server over a secure WebSocket (`wss://`). It is not
  captured when you are not recording.
- **Your sign-in credentials.** When you sign in, your email and password are sent **once** to your
  configured server to obtain a device token. Your password is **not stored** by the extension.
- **A device token + your server URL**, stored locally in the browser (`chrome.storage.local`) so
  you stay signed in between meetings. These never leave your browser except to authenticate with
  your configured server.

## Where data goes

The extension sends data **only to the minutes server URL you configure**. It does **not** send your
audio, credentials, or any usage data to the extension's author or to any third-party analytics,
advertising, or tracking service. The extension contains no analytics or tracking.

Your minutes server, in turn, uses two processors to do its job — **Soniox** (speech-to-text) and
**Anthropic** (translation) — under the server operator's own agreements with those providers. How
your meeting data is stored and retained on the server is controlled by **the operator of that
server** (your organization), who is the data controller. See your server operator's own policies
for retention and access.

## Permissions and why they're needed

- **`tabCapture` / `offscreen`** — to capture the meeting tab's audio and encode it for streaming.
- **`activeTab` / `storage`** — to detect the meeting on the current tab and to remember your server
  URL + device token.
- **Host access (your server + meet.google.com / teams.microsoft.com)** — to sign in and stream to
  the server URL you enter, and to detect meetings on Meet/Teams. The extension needs to reach an
  arbitrary server address because **you** choose where your self-hosted minutes instance lives.

## Your choices

- **Sign out** from the popup at any time — this stops capture and deletes the device token from the
  browser.
- **Recording is explicit** — capture only runs while you have pressed *Start recording*, and the
  toolbar icon shows a recording state throughout.
- Removing the extension deletes its locally stored data (server URL + device token).

## Contact

This is open-source software ([github.com/arjmandi/minutes](https://github.com/arjmandi/minutes)).
For questions about how a specific minutes instance handles your data, contact the operator of that
server. For the extension itself, open an issue on the GitHub repository.
