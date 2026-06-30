---
sidebar_position: 4
title: "Meetings, translation & export"
---

# Meetings, translation & export

Once a meeting is being captured, everything happens in the minutes web app. This page walks through reading a live meeting, turning on translation, fixing or filling in individual lines, renaming a meeting, copying a transcript to your clipboard, and downloading one to keep.

## The three-column view

Open the app and you get a single screen split into three columns (on a phone they collapse into one view at a time — see [On your phone](/users/web-app#on-your-phone)):

- **Transcriptions** (left) — every meeting you own, newest first. Click one to open it; the **⟳** button reloads the list. (Admins see everyone's.)
- **Transcript** (middle) — the spoken words, line by line, as they're recognised.
- **Translation** (right) — the translated text for each line, lined up next to the original.

The translation column stays in step with the transcript: each translated line sits on the same row as the line it came from, so you can read across.

:::tip
You don't have to wait for a meeting to end. While a meeting is live, new lines stream into the transcript on their own — there's nothing to refresh.
:::

:::note Two audio sources
A meeting can carry two separate audio sources — **Online stream** (the captured tab) and **Host mic** (your own microphone) — each with its own transcript and translations. When it does, a switcher in the meeting header flips both columns between them. See [Two audio sources in one meeting](/users/web-app#two-audio-sources-in-one-meeting). You can also pick which source to **export**, below.
:::

### Interim vs final lines

Speech recognition works in two passes:

- An **interim** line appears in faint italic text at the bottom of the transcript while someone is still talking. It's a best guess and it will change as more words arrive.
- When the speaker pauses, that guess is finalised into a **final** line — the interim text disappears and a clean, permanent line takes its place in the transcript.

Only final lines are saved, translated, and exported. Interim text is just a live preview, so don't be surprised when it shifts around before settling.

### Persian and other right-to-left text

Persian (`fa`) is a right-to-left language. minutes detects this and flips any Persian line so it reads correctly — the text aligns to the right and the timestamp gutter mirrors to match. This applies to both the original transcript and the translation, on whichever side the Persian text appears. English (`en`) and German (`de`) read left-to-right as usual.

### Timestamps

Every line carries a timestamp. In the live view, this is the **absolute wall-clock time** the line was spoken (for example `14:32:05`), based on when the capture session joined the call — handy for matching a quote back to a moment in the meeting. Exports use a slightly different style; see [Timestamps in exports](#with-or-without-timestamps) below.

## Translation

Translation is per-meeting and entirely optional. It runs on **your own Anthropic key**, which you set once under **Settings → API keys**. No key means no translation — the feature simply stays off, and no text is ever sent to Anthropic.

:::note
Translation (live and on-demand) always uses the meeting **owner's** Anthropic key. If you haven't added one yet, see [Signing in & your settings](/users/web-app) to set it up. Soniox handles speech-to-text; Anthropic only ever sees text for translation.
:::

### Turn it on and pick a language

1. Open the meeting.
2. Click the gear icon (⚙) in the **Translation** column header.
3. Flip **Enabled** on, choose an **Output language** — English (`en`), German (`de`), or Persian (`fa`) — optionally pick a **Model** (Haiku / Sonnet / Opus), and click **Save**.

The column header updates to show what you're translating into, for example `Translation → de`. From then on, every new final line in this meeting is translated into that language as it arrives.

:::note
A translation toggle applies to the **next** capture session and to on-demand lines. Turning translation on partway through a live meeting takes effect for new lines going forward — it doesn't retroactively translate everything already on screen. Use the on-demand "translate" action below to fill in earlier lines.
:::

:::warning
You can only enable translation if an output language is chosen — there's no "translate into nothing." Pick a language first, then enable.
:::

### "Translate this line" on demand

Any line that doesn't have a translation yet shows a small **translate** link in the translation column. Click it and minutes translates just that one line into the meeting's output language, right away. The result is saved and stays on the line.

This is the way to translate lines from before you turned translation on, or to translate the occasional line in a meeting where you don't want full translation running.

### When a translation fails

If a line can't be translated — a network hiccup, a quota limit, an Anthropic error — that row shows **translation failed** with a **retry** link. Click **retry** to try that single line again. Nothing else is affected, and the rest of the transcript keeps flowing normally.

If on-demand translation reports that translation is unavailable, it usually means there's no Anthropic key on the account — add one under **Settings → API keys** and try again.

## Rename a meeting

Meetings start out named after their underlying call identifier, which isn't very readable. To give one a proper name:

1. Open the meeting.
2. Click the **title** at the top of the transcript column (it's editable — your cursor turns into a text cursor over it).
3. Type a new name and press **Enter** to save, or **Escape** to cancel.

The new name shows up everywhere — the meetings list, the export filename, and any [public share link](/users/sharing). Only you (the owner) or an admin can rename a meeting.

## Copy vs. export

Two ways to get a transcript out of minutes, for two different needs:

- **Copy** — a one-click **plain-text grab**, straight to your clipboard, with **no timestamps**. It copies the source you're currently viewing (transcript or translation, and — in a [two-source meeting](/users/web-app#two-audio-sources-in-one-meeting) — whichever source the switcher is on). Perfect for pasting into an email, a chat, or your notes. The copy icons live in the **Transcript** and **Translation** column headers (on a phone, in the meeting header). See [Copy the transcript or translation](/users/web-app#copy-the-transcript-or-translation).
- **Export** — a **downloadable file** with options: pick the format (`txt` / `md` / `json`), what to include (transcript / translation / both), timestamps on or off, and (for a two-source meeting) which source. Use it when you want a file to keep, archive, or hand off.

The rest of this section covers **export**.

## Export

Click **Export** at the top of a meeting to download it as a file you can keep, archive, or hand to someone. minutes can produce three formats, and you can choose what goes in each file. (For a quick clipboard grab without timestamps, [Copy](#copy-vs-export) is faster.)

### Formats: txt, md, json

| Format | File | What you get |
| --- | --- | --- |
| **`txt`** | plain text | One line per spoken segment, with the speaker label and (optionally) a timestamp in front. The simplest, most portable form. |
| **`md`** | Markdown | The same lines as `txt`, but with the meeting **title as a heading** and a small subheading naming the platform and call. Good for pasting into notes or a wiki. |
| **`json`** | structured data | A machine-readable object with a `meeting` block (title, platform, timing, translation settings) and a `segments` array — every line with its text, speaker, timestamps, source language, and all translations. Use this if you want to process the transcript with another tool. |

The downloaded file is named after the meeting **title** (with unsafe characters stripped); a meeting that was never renamed falls back to a neutral name.

### Audio source: Both, Online stream, or Host mic

If the meeting has **two audio sources** (see [Two audio sources in one meeting](/users/web-app#two-audio-sources-in-one-meeting)), the export dialog gains an **Audio source** choice:

- **Both** — every source in one file, written as **labeled sections**, one per source.
- **Online stream** — only the captured tab's lines.
- **Host mic** — only your microphone's lines.

This choice only appears for meetings that actually have both sources; a single-source meeting (including any [upload](/users/uploads)) exports as before, with no source picker. It **composes** with everything else here — pick a format, decide what content to include, and turn timestamps on or off, all independently of which source(s) you export.

### Include: transcript, translation, or both

You can choose what content the file carries:

- **transcript** — only the original spoken text.
- **translation** — only the translated text (in the meeting's output language).
- **both** — the original line, with its translation indented directly beneath it.

In the `json` format every translation is always present in each segment regardless of this choice, since `json` is the complete structured record.

:::note
"translation" and "both" only have translated text to show for lines that were actually translated. Lines you never translated come out blank on the translation side. Translate them first (live translation or the on-demand **translate** link) if you want them in the export.
:::

### With or without timestamps

Exports can include a timestamp in front of each line or leave it off. In a file, the timestamp is the **elapsed time since the meeting started**, written as `[HH:MM:SS]` — so `[00:03:12]` is three minutes and twelve seconds in. (This is different from the live view, which shows the absolute clock time.) Turn timestamps off for a clean, prose-style transcript.

:::tip
The Export button in the app gives you a one-click download of each format with transcript **and** translation included and timestamps **on**. The export endpoint itself also supports transcript-only, translation-only, timestamp-free, and per-source variants for anyone scripting downloads against the API.
:::

## Related

- **[Sharing a transcript](/users/sharing)** — hand a read-only web page (and downloadable export) to someone without an account.
- **[Transcribing an audio file](/users/uploads)** — drop in a recording instead of capturing a live call; the same transcript, translation, and export tools apply.
