// Offscreen document: run one audio pipeline PER SOURCE (dual-source capture) and stream framed
// PCM to the backend ingest WS. Wire format matches app/audio/frames.py: 13-byte LE header
// (seq uint32, ts_ms uint64, flags uint8) + Int16 PCM (s16le). Auth via the
// `minutes.auth.bearer` subprotocol so the token never lands in the URL.
//
// Each capture source ("tab" = the meeting/video tab; "mic" = the host's microphone) is an
// independent Capture: its own getUserMedia stream, AudioWorklet chain, and WebSocket — so the two
// never share a failure domain. They share ONE AudioContext. The tab is played back to the user
// (passthrough); the mic is NOT (that would echo the tab into the mic). Per-source status is
// published to chrome.storage.local.minutesCapture.sources for the popup + the toolbar icon.

const AUTH_SUBPROTOCOL = "minutes.auth.bearer";

const captures = new Map(); // source -> Capture
const failed = new Map(); // source -> error detail, for sources that died (kept after teardown so
                          // the popup can show a per-source error chip while the other keeps going)
let ctx = null;
let workletReady = null;

async function ensureCtx() {
  if (!ctx) ctx = new AudioContext();
  if (!workletReady) workletReady = ctx.audioWorklet.addModule("pcm-worklet.js");
  await workletReady;
  return ctx;
}

// Serialize the live captures' per-source status to storage (the popup + background read it).
function publishStatus() {
  const sources = {};
  for (const [src, c] of captures) {
    sources[src] = { capturing: c.live, frames: c.frames, db: c.lastDb, status: c.status };
  }
  // Sources that ended on an error (e.g. Soniox rejected a 2nd concurrent connection) stay visible
  // as error chips while the OTHER source keeps recording. Cleared when that source restarts.
  for (const [src, detail] of failed) {
    if (!sources[src]) sources[src] = { capturing: false, error: true, status: detail };
  }
  try {
    chrome.storage.local.set({ minutesCapture: { sources, at: Date.now() } });
  } catch {}
  // Direct push to the background (toolbar icon) + popup (chips). storage.onChanged alone proved
  // unreliable to wake/deliver promptly, so this message is the authoritative live-update path.
  try { chrome.runtime.sendMessage({ type: "capture-status", sources }).catch(() => {}); } catch {}
}

function rmsDb(int16) {
  let sum = 0;
  for (let i = 0; i < int16.length; i++) {
    const v = int16[i] / 32768;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / int16.length);
  return rms > 0 ? Math.round(20 * Math.log10(rms)) : -99;
}

function encodeFrame(seqNo, tsMs, int16, gap) {
  const buf = new ArrayBuffer(13 + int16.byteLength);
  const dv = new DataView(buf);
  dv.setUint32(0, seqNo >>> 0, true);
  dv.setBigUint64(4, BigInt(Math.trunc(tsMs)), true);
  dv.setUint8(12, gap ? 1 : 0); // flags: bit0 = gap-before-this-frame (silence-suspend resume)
  new Uint8Array(buf, 13).set(new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength));
  return buf;
}

// Silence-suspend (Host mic only): stop streaming during sustained silence to cut Soniox billing.
// The backend re-adds the dropped time to segment timestamps via the real ts_ms each frame carries.
const SILENCE_DB = -50;        // below this (dBFS) a 100 ms frame counts as silence
const HANGOVER_FRAMES = 25;    // keep streaming 2.5 s into silence before suspending (don't clip tails)
const PREROLL_FRAMES = 4;      // 400 ms of lead-in replayed on resume so word onsets aren't clipped
const KEEPALIVE_FRAMES = 50;   // while suspended, send one frame every 5 s to keep the WS + STT warm

class Capture {
  constructor(source) {
    this.source = source; // "tab" | "mic"
    this.ws = null;
    this.node = null;
    this.srcNode = null;
    this.stream = null;
    this.seq = 0;
    this.total = 0;
    this.frames = 0;
    this.lastReport = 0;
    this.lastDb = -99;
    this.stopping = false;
    this.ended = false;
    this.cleanEnded = false; // backend acked "ended" — a clean finish, not a failure
    this.failDetail = null; // set on an error termination -> kept in `failed` for the popup
    this.live = true; // present-in-map + not torn down
    this.status = "starting…";
    // silence-suspend state (Host mic only; set in start())
    this.silenceOn = false;
    this.suspended = false;
    this.silentRun = 0;
    this.preroll = [];
    failed.delete(source); // a fresh start clears any prior error chip for this source
  }

  // Send one PCM frame to the backend (counts toward the live frame total / status).
  _send(int16, tsMs, gap) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(encodeFrame(this.seq++, tsMs, int16, gap));
    this.frames++;
    if (this.frames - this.lastReport >= 10) {
      this.lastReport = this.frames;
      this.lastDb = rmsDb(int16);
      this.status = "capturing · " + this.frames + " frames · " + this.lastDb + " dBFS";
      publishStatus();
    }
  }

  async start(spec) {
    await ensureCtx();
    // Silence-suspend applies to the Host mic only (the Online stream is continuous content).
    if (this.source === "mic") {
      try { this.silenceOn = (await chrome.storage.local.get("silenceSuspend")).silenceSuspend !== false; }
      catch { this.silenceOn = true; }
    }
    try {
      if (this.source === "tab") {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: spec.streamId } },
        });
      } else {
        // Mic. echoCancellation defaults OFF: with the tab playing on speakers, AEC's reference is
        // the tab audio and can suppress the host's speech (headphone users can leave it off too).
        const audio = { echoCancellation: !!spec.aec, noiseSuppression: true, autoGainControl: true };
        if (spec.deviceId) audio.deviceId = { exact: spec.deviceId };
        this.stream = await navigator.mediaDevices.getUserMedia({ audio });
      }
    } catch (err) {
      this.status = "capture_failed: " + err;
      this.teardown();
      return;
    }

    this.srcNode = ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(ctx, "pcm-downsampler");
    this.srcNode.connect(this.node);
    if (this.source === "tab") this.srcNode.connect(ctx.destination); // passthrough; mic must NOT

    this.ws = new WebSocket(spec.config.backendUrl, [AUTH_SUBPROTOCOL, spec.config.token]);
    this.ws.binaryType = "arraybuffer";
    this.ws.onopen = () => {
      this.status = "connected — waiting for admission…";
      publishStatus();
      this.ws.send(
        JSON.stringify({
          type: "hello",
          platform: spec.config.platform,
          external_meeting_id: spec.config.externalMeetingId,
          call_id: spec.config.callId,
          source: this.source,
        })
      );
    };
    this.ws.onmessage = (ev) => {
      let data;
      try {
        data = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (data.type === "admitted") {
        this.status = "capturing — 0 frames";
        publishStatus();
      } else if (data.type === "ended") {
        this.status = "ended · " + data.segments + " segments";
        this.cleanEnded = true;
        this.teardown();
      } else if (["rejected", "conflict", "forbidden", "error"].includes(data.type)) {
        // Surface the backend's actionable detail (e.g. Soniox quota/billing) to the popup.
        this.status = "denied · " + (data.detail || data.reason || data.type);
        this.failDetail = data.detail || data.reason || "Transcription was rejected.";
        this.live = false;
        publishStatus();
        this.teardown();
      }
    };
    this.ws.onerror = () => {
      this.status = "ws_error";
      publishStatus();
    };
    this.ws.onclose = () => {
      if (!this.stopping) {
        this.status = "disconnected";
        // An abnormal drop (not a user stop, not a clean backend "ended") is an error to surface.
        if (!this.cleanEnded && !this.failDetail) this.failDetail = "Connection lost.";
      }
      this.teardown();
    };
    this.node.port.onmessage = (e) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      const int16 = e.data; // Int16Array, 1600 samples (100 ms @ 16 kHz)
      const tsMs = this.total / 16; // 16 samples per ms at 16 kHz — REAL elapsed time (counts silence)
      this.total += int16.length;

      if (!this.silenceOn) { this._send(int16, tsMs, false); return; }

      // Silence-suspend: don't stream sustained silence; resume (with a short pre-roll) on speech.
      const loud = rmsDb(int16) >= SILENCE_DB;
      if (loud) {
        if (this.suspended) {
          let first = true; // the first frame after a gap carries the gap flag
          for (const f of this.preroll) { this._send(f.int16, f.tsMs, first); first = false; }
          this.suspended = false;
          this.preroll = [];
          this._send(int16, tsMs, first);
        } else {
          this._send(int16, tsMs, false);
        }
        this.silentRun = 0;
      } else {
        this.silentRun++;
        if (this.suspended) {
          this.preroll.push({ tsMs, int16 });
          if (this.preroll.length > PREROLL_FRAMES) this.preroll.shift();
          // Periodic keepalive so the WS + STT connection stay warm through long silences.
          if (this.silentRun % KEEPALIVE_FRAMES === 0) this._send(int16, tsMs, true);
        } else if (this.silentRun > HANGOVER_FRAMES) {
          this.suspended = true;
          this.preroll = [{ tsMs, int16 }];
          this.status = "suspended (silence) · " + this.frames + " frames";
          publishStatus();
        } else {
          this._send(int16, tsMs, false); // hangover — keep streaming briefly into the pause
        }
      }
    };
  }

  stop() {
    this.stopping = true;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "end" })); // backend finalizes, replies "ended"
    } else {
      this.teardown();
    }
  }

  teardown() {
    if (this.ended) return; // exactly once
    this.ended = true;
    this.live = false;
    try { if (this.node) this.node.disconnect(); } catch {}
    try { if (this.srcNode) this.srcNode.disconnect(); } catch {}
    try { if (this.stream) this.stream.getTracks().forEach((t) => t.stop()); } catch {}
    try { if (this.ws) this.ws.close(); } catch {}
    this.ws = this.node = this.srcNode = this.stream = null;
    captures.delete(this.source);
    if (this.failDetail) failed.set(this.source, this.failDetail); // keep the error chip visible
    publishStatus();
    if (captures.size === 0) {
      // All sources done: release the shared context and have the background close this doc.
      try { if (ctx) { ctx.close(); ctx = null; workletReady = null; } } catch {}
      chrome.runtime.sendMessage({ type: "capture-ended" }).catch(() => {});
    }
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== "offscreen") return;
  if (msg.type === "start" || msg.type === "add-source") {
    const source = msg.source || "tab";
    if (captures.has(source)) return; // already capturing this source
    const cap = new Capture(source);
    captures.set(source, cap);
    cap.start({ streamId: msg.streamId, deviceId: msg.deviceId, aec: msg.aec, config: msg.config });
  } else if (msg.type === "stop" || msg.type === "remove-source") {
    if (msg.source) {
      const c = captures.get(msg.source);
      if (c) c.stop();
    } else {
      for (const c of [...captures.values()]) c.stop(); // stop everything
    }
  }
});
