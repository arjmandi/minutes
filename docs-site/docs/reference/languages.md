---
sidebar_position: 1
title: "Languages"
---

# Languages

minutes supports three languages end to end — for speech-to-text, for translation, and for rendering:

| Language | Code | Direction |
| --- | --- | --- |
| English | `en` | left-to-right |
| German | `de` | left-to-right |
| Persian / Farsi | `fa` | **right-to-left** |

These are the language hints sent to Soniox for live capture (`MINUTES_LANGUAGE_HINTS`, default `["en", "de", "fa"]`) and the choices offered as a meeting's translation output language.

## Input language is detected automatically

You do not pick the spoken language for a meeting. Live capture streams audio to Soniox with **automatic language identification** turned on, so each finalized line is tagged with the language Soniox detected for it. A single meeting can mix languages line by line — one speaker in German and another in English will each be transcribed in their own language.

:::note Mixed-language meetings just work
Because detection is per line, you don't configure an "input language." A meeting's translation settings do expose an optional input-language field, but it defaults to `detect` — leave it alone and Soniox decides per line.
:::

## One output language per meeting

Translation has a single **output language** per meeting. When a meeting has translation enabled and an output language set, every newly finalized line is translated into that one language using the meeting owner's Anthropic key (see [Meetings, translation & export](/users/meetings-and-export)). Lines already in the output language are left as-is — nothing is "translated" from a language into itself.

- Set the output language per meeting, or set a personal default that new meetings inherit, in **Settings**.
- Changing the output language (or the custom prompt/model) mid-meeting applies to the **next** capture session, not retroactively.
- Need a one-off line in a different language? Use the per-line **"translate this line"** action, which can target any supported language on demand.

For the full picture — enabling translation, custom prompts, and the on-demand action — see [Meetings, translation & export](/users/meetings-and-export). For why a meeting is limited to one output language, see [Limitations](/reference/limitations).

## Right-to-left (Persian) rendering

When a line's detected language — or a translation's target language — is Persian (`fa`), minutes renders it right-to-left. In the web app this flips the line's text direction and alignment and mirrors the timestamp gutter, so Persian transcript and translation lines read correctly without any setup.

RTL is also preserved in **exports**: exported transcripts and translations keep the original text exactly as captured, so the right-to-left ordering carries through to `txt`, `md`, and `json` output. (Markdown and plain-text viewers display the direction based on the characters themselves.) See [Meetings, translation & export](/users/meetings-and-export) for formats and options.

:::tip Other RTL scripts
The app's RTL rendering also recognizes Arabic, Hebrew, Urdu, and Pashto if such text ever appears, but only English, German, and Persian are officially supported for transcription and translation.
:::
