---
sidebar_position: 5
title: "Sharing a transcript"
---

# Sharing a transcript

Sometimes the people who need to read a meeting transcript don't have an account on your minutes server — a client, a colleague on another team, someone who just wants to skim what was said. **Public share links** let you hand them a read-only web page they can open in any browser, no sign-in required.

You stay in control: you decide when a link exists, you can swap it for a fresh one at any time, and you can turn it off completely.

## How a share link works

A share link is a single, hard-to-guess URL of the form:

```text
https://your-domain.example/shared/<token>
```

The `<token>` is a long, random, opaque string. There's no way to guess it or list other people's links — if you don't have the exact URL, you can't reach the transcript. Anyone who **does** have the URL can open the page and read the meeting, even without an account on your server.

Each meeting has at most one active link at a time.

## Enable a link

1. Open the meeting you want to share.
2. Open the **Public share link** panel for that meeting.
3. Click **Enable share link**.

minutes mints a token and shows you the full URL with a copy button. Click the copy button, then paste the link to whoever needs it.

:::tip
The link points at a page on your own minutes server. Whoever opens it just needs a browser and the URL — they never create an account, sign in, or install the capture extension.
:::

## What a viewer sees

Open the link and you get a clean, read-only view of the meeting. It shows:

- The **meeting title**
- The **platform** it came from (Google Meet or Microsoft Teams)
- **Timestamps** for each line
- The full **transcript**, and the **translation** if translation was enabled for that meeting

Each line shows its translation **inline, right beneath the original**, so a reader can follow both at once.

That's it — there's nothing to edit, no controls to start or stop anything. It's a snapshot of the conversation for reading.

### Meetings with two audio sources

If the meeting was captured with **two audio sources** — **Online stream** (the captured tab) and **Host mic** (the host's own microphone) — the shared page lays them out as **two labeled columns**, one per source, each headed with its name. Every line still carries its translation inline beneath it.

A meeting with a **single** source (including any [uploaded file](/users/uploads)) stays a **single column**, exactly as before.

The layout adapts to the reader:

- **Right-to-left languages** (such as Persian) render correctly — the text and its gutter mirror to read right-to-left.
- **On a phone**, the two columns **stack vertically** instead of sitting side by side, so each is comfortable to read on a narrow screen.

## What a viewer never sees

The public page is deliberately stripped down. A viewer holding the link can **not** see:

- **Who owns the meeting** — no name, no email, no account identity.
- **The external meeting id** — the underlying Meet/Teams call identifier is never exposed (it's also kept out of any file a viewer exports).
- **Any other meeting** — the token only unlocks the one meeting it belongs to. It reveals nothing about your other meetings or their links.

Internal details like consent state and the private translation configuration (your custom prompt or model choice) stay private too. The page exposes only what's needed to read the conversation.

## Viewers can export too

The public viewer isn't read-only-on-screen-only — anyone with the link can also **download the transcript**, the same way you can from your own meeting list. The export covers the transcript, the translation, or both, in plain text, Markdown, or JSON.

Exports from a share link carry the same privacy guarantees as the page itself: the file is named after the meeting title (never the external meeting id), and the owner identity is never included.

For the full set of export formats and options, see [Meetings and export](/users/meetings-and-export).

## Rotate = issue a new link, revoke the old one

Shared a link too widely? Sent it to the wrong person? Click **Rotate**.

Rotating mints a **brand-new token** and immediately retires the old one. The previous URL stops working — anyone still holding it gets a "not found" page — and you're handed the fresh link to share with the people who should still have access.

:::warning
Rotating is how you **revoke** a link. There's no separate "expire" step: the moment you rotate, every copy of the old URL that's floating around in emails or chats goes dead.
:::

## Disable = turn sharing off

Click **Disable** to remove the link entirely. The meeting goes back to being private to you (and your admins). The URL stops resolving for everyone.

You can enable a link again later if you change your mind — but you'll get a **new** token, so the old URL won't come back to life.

:::note
Enabling, rotating, and disabling a share link are all things only **you** (the meeting owner) or an admin can do. Viewers can only read and export.
:::
