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

## Part B — bot-join driver (`driver/`)

Playwright harness: launches headed Chrome with this extension + a persistent profile (the bot
account stays signed in across runs), joins the invited Meet **or** Teams meeting, and triggers
capture for that tab via the content script — unattended, no popup click.

```bash
cd capture/driver && npm install && npx playwright install chromium
MINUTES_CAPTURE_TOKEN=$(curl -sXPOST localhost:8000/auth/dev-token \
  -H 'content-type: application/json' -d '{"principal":"bot","meetings":["*"]}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])') \
  node join.mjs "https://meet.google.com/abc-defg-hij"
```

The **first run** opens a login window — sign the bot account in once; the persistent profile
(`driver/.profiles/<platform>/`) reuses it afterward. For **Teams**, pass a
`https://teams.microsoft.com/...` URL: the bot must be invited, and for external-org meetings it
may land in the lobby (a present host/presenter admits it — see the external-capture analysis).

> ⚠️ **Needs live validation:** the join selectors and login flow per platform will need tuning
> against the current Meet/Teams pre-join UIs. The launch + extension + profile + capture-trigger
> plumbing is the stable core; the driver always attempts capture after a settle window even if a
> join selector misses, so you can complete the join by hand.
