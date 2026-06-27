# minutes — capture client

Local capture client (spec v3 §4). A real participant (or the dedicated bot account) runs this on
their machine; it captures the meeting **tab** audio and streams 16 kHz mono PCM to the backend
ingest WebSocket. No server-side bot joins the meeting, so there is nothing for the host platform
to detect or lobby — it works for external-org meetings.

## Part A — Chrome MV3 extension (`extension/`)

`tabCapture` → offscreen document → AudioWorklet (downmix + downsample to 16 kHz mono Int16) →
framed PCM (matches `app/audio/frames.py`: 13-byte LE header + s16le) → authenticated `wss://`
ingest. Auth token is passed via the `minutes.auth.bearer` subprotocol (never in the URL).

**Manual validation (until the Part B driver lands):**
1. Run the backend (`uv run uvicorn app.main:app --reload`) with Postgres + Redis up.
2. Mint a dev token: `curl -s -XPOST localhost:8000/auth/dev-token -H 'content-type: application/json' -d '{"principal":"me","meetings":["*"]}'`
3. `chrome://extensions` → enable Developer mode → **Load unpacked** → select `capture/extension`.
4. Join a Google Meet (`meet.google.com/...`) in a tab.
5. Click the extension → paste the token (backend URL defaults to `ws://localhost:8000/ingest`,
   meeting id auto-detected from the tab) → **Start capture**. Speak; transcripts persist via the
   pipeline. **Stop** finalizes and shows the segment count.

> ⚠️ **Needs in-Chrome validation** (written against MV3 docs; not yet run in a browser here),
> mirroring how the Soniox client was validated live. Likely things to verify/iterate: offscreen
> `getUserMedia({chromeMediaSource:"tab"})` constraints on current Chrome, the worklet resampler
> quality, and the passthrough so the human still hears the meeting.

## Part B — bot-join driver (next)

A Playwright harness that launches a real (headed) Chrome with this extension + a persistent
profile, signs the **dedicated bot Google account** in (one-time interactive login → persisted
`storageState`), joins the invited Meet URL, and triggers capture — so capture runs unattended
without a human clicking the popup. Teams web join follows after Meet.
