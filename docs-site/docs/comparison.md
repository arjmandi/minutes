---
sidebar_position: 2
title: "How minutes compares"
---

# How minutes compares

Most meeting note-takers — Otter, Fireflies, Fathom, tl;dv, Granola, MeetGeek and friends — are **cloud SaaS**: you sign up, a bot or their cloud records your calls, and your audio, transcripts, and summaries live on the vendor's servers under their terms, billed per seat.

**minutes takes the opposite approach.** It's **open-source** software *you* run on *your* server, with *your* speech-to-text and translation API keys. Your meeting audio and transcripts stay on infrastructure you control, in the region you choose. That's the whole point — and the main trade-off (you operate a server).

This page is here to help you choose honestly, including where the mature SaaS tools are simply further along.

## At a glance

| | **minutes** | **Typical cloud note-taker**¹ |
| --- | --- | --- |
| **Hosting** | Self-hosted (your VPS / your cloud) | Vendor's cloud |
| **Source** | Open-source (MIT) | Proprietary |
| **Where your data lives** | Your server, your region (EU-friendly) | Vendor's cloud, often US |
| **Pricing model** | Your infra cost + your STT/LLM API usage, billed by the providers directly — no per-seat fee | Per-seat subscription (commonly ~$10–30/user/mo) |
| **API keys** | Bring your own (Soniox for STT, Anthropic for translation) | Bundled into the subscription |
| **Capture** | Browser extension grabs the **meeting tab's audio** — no bot joins the call | Often a bot that joins as a visible participant, or vendor cloud capture |
| **Live translation** | Built in — English, German, Persian (incl. right-to-left), live + on uploads | Varies; usually transcription + summaries, translation less common |
| **GDPR controls** | Consent gate, retention purge, and per-meeting erasure built in; data never leaves your box except to your STT/LLM providers | Varies by vendor and plan |
| **Lock-in** | None — plain Postgres + object storage you own; export to txt/md/json | Export varies; data in the vendor's system |

¹ "Typical cloud note-taker" is a generalization of the popular SaaS tools as of 2026. Vendors differ and change quickly — **check the current details on each product's own site** (see sources below). Some, like Fellow, advertise SOC 2 / GDPR / HIPAA; a few offer EU hosting. The structural point stands regardless: in a SaaS tool your meeting data is processed and stored by the vendor; with minutes it is processed and stored by you.

## Where minutes is different

- **You own the data.** Audio and transcripts sit in your Postgres and your object store. The only third parties that ever see your content are the two you choose and pay directly: **Soniox** (audio → text) and **Anthropic** (text → translation). Nothing else leaves your box.
- **It's open-source.** Read the code, audit it, change it, self-host it forever. MIT licensed.
- **No per-seat tax.** You pay your VPS (~a few dollars/month for the showcase box) and your actual Soniox/Anthropic usage. Adding users doesn't add a subscription line.
- **No bot in the meeting.** The capture extension streams the tab's audio from a participant who's already in the call — there's no separate "Notetaker" bot showing up in the participant list.
- **Live, multilingual.** Transcribe and translate *as the meeting happens* across English/German/Persian, with proper right-to-left rendering — not just an after-the-fact summary.
- **GDPR by construction.** EU region, a consent gate, retention purges, and one-click meeting erasure are part of the product, not an enterprise add-on.

## Where the SaaS tools are ahead

Being fair: the established products have had years and large teams. Today they generally do these better than minutes:

- **AI summaries, action items, and "ask your meetings" chat** — minutes focuses on an accurate live transcript + translation; rich post-meeting AI is not its focus (yet).
- **Integrations** — CRM sync, calendar auto-join, Slack/Notion/Drive exports, Zapier. minutes exports files; it doesn't wire into your stack.
- **Mobile apps and polish** — minutes is a web app + a Chrome extension. No iOS/Android app.
- **Language coverage** — minutes targets English, German, and Persian. The big tools support dozens.
- **Zero-ops** — they're sign-up-and-go. minutes asks you to run a server, manage users, and bring API keys.
- **Maturity & support** — minutes is a focused v1; the default single-box deploy is a low-cost showcase with no built-in HA or backups (see [Limitations](/reference/limitations)).

## Choose minutes if…

- You (or your customers) **must keep meeting data in-house or in the EU** for privacy/GDPR/contractual reasons.
- You want **open-source** software you can audit and self-host, with **no per-seat pricing**.
- You mainly need an **accurate live transcript + live translation** across English/German/Persian, and you can run a small server.

## Choose a cloud SaaS tool if…

- You want **zero infrastructure** and instant setup.
- You need **deep integrations, polished AI summaries, mobile apps, or very broad language support** today.
- Sending meeting data to a third-party cloud is acceptable for your use case.

---

**Sources** (AI meeting-notetaker comparisons, 2026 — verify current pricing/features on each vendor's site):

- [7 AI Meeting Notetakers Tested in 2026 (get-alfred.ai)](https://get-alfred.ai/blog/best-ai-meeting-notetakers)
- [Otter vs Fireflies vs Fathom (index.dev)](https://www.index.dev/blog/otter-vs-fireflies-vs-fathom-ai-meeting-notes-comparison)
- [Best AI Note Takers 2026 (meetingnotes.com)](https://meetingnotes.com/blog/best-ai-note-takers)
- [Granola vs Otter vs Fireflies vs Fathom 2026 (useluminix.com)](https://www.useluminix.com/reports/industry-analysis/ai-meeting-notes-comparison-granola-vs-otter-vs-fireflies-vs-fathom-2026)
