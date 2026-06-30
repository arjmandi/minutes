// In-app mic recorder for the web app / PWA ("New Recording"). A second capture client alongside the
// Chrome extension — same /ingest contract (source:"mic"), same 13-byte frame header + s16le PCM,
// same pcm-downsampler worklet. Single source (Host mic); the token is minted from the cookie session.
// Mirrors the extension offscreen Capture: getUserMedia -> AudioContext -> worklet -> WS, with
// silence-suspend (extension-parity) and a Screen Wake Lock while recording.
(function () {
  const AUTH_SUBPROTOCOL = "minutes.auth.bearer";
  const SILENCE_DB = -50, HANGOVER_FRAMES = 25, PREROLL_FRAMES = 4, KEEPALIVE_FRAMES = 50;

  function encodeFrame(seq, tsMs, int16, gap) {
    const buf = new ArrayBuffer(13 + int16.byteLength);
    const dv = new DataView(buf);
    dv.setUint32(0, seq >>> 0, true);
    dv.setBigUint64(4, BigInt(Math.trunc(tsMs)), true);
    dv.setUint8(12, gap ? 1 : 0); // flags bit0 = gap-before-this-frame (silence-suspend resume)
    new Uint8Array(buf, 13).set(new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength));
    return buf;
  }
  function rmsDb(int16) {
    let sum = 0;
    for (let i = 0; i < int16.length; i++) { const v = int16[i] / 32768; sum += v * v; }
    const rms = Math.sqrt(sum / int16.length);
    return rms > 0 ? 20 * Math.log10(rms) : -99;
  }

  class Recorder {
    // opts: { wsUrl, token, platform, externalMeetingId, callId, deviceId, aec, silence,
    //         onState(state,detail), onLevel(0..1) }
    constructor(opts) {
      this.o = opts;
      this.ws = null; this.ctx = null; this.node = null; this.srcNode = null; this.stream = null;
      this.seq = 0; this.total = 0; this.stopping = false; this.ended = false; this.cleanEnded = false;
      this.silenceOn = opts.silence !== false;
      this.suspended = false; this.silentRun = 0; this.preroll = [];
      this.wakeLock = null;
    }

    async start() {
      this.o.onState?.("starting");
      // AudioContext MUST be created/resumed inside the user gesture (iOS starts it suspended).
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      try { await this.ctx.resume(); } catch { /* ignore */ }
      const audio = { echoCancellation: !!this.o.aec, noiseSuppression: true, autoGainControl: true, channelCount: 1 };
      if (this.o.deviceId) audio.deviceId = { exact: this.o.deviceId };
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio });
      } catch (err) {
        this.o.onState?.("error", err && err.name === "NotAllowedError" ? "mic_denied" : ("mic_failed: " + (err?.message || err)));
        this.teardown();
        return;
      }
      await this.ctx.audioWorklet.addModule("/assets/pcm-worklet.js");
      this.srcNode = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "pcm-downsampler"); // mic: NOT connected to destination (no echo)
      this.srcNode.connect(this.node);
      this._acquireWakeLock();

      this.ws = new WebSocket(this.o.wsUrl, [AUTH_SUBPROTOCOL, this.o.token]);
      this.ws.binaryType = "arraybuffer";
      this.ws.onopen = () => {
        this.ws.send(JSON.stringify({
          type: "hello", platform: this.o.platform, external_meeting_id: this.o.externalMeetingId,
          call_id: this.o.callId, source: "mic",
        }));
      };
      this.ws.onmessage = (ev) => {
        let d; try { d = JSON.parse(ev.data); } catch { return; }
        if (d.type === "admitted") this.o.onState?.("recording");
        else if (d.type === "ended") { this.cleanEnded = true; this.o.onState?.("ended", { segments: d.segments }); this.teardown(); }
        else if (["rejected", "conflict", "forbidden", "error"].includes(d.type)) {
          this.o.onState?.("error", d.detail || d.reason || d.type);
          this.teardown();
        }
      };
      this.ws.onclose = () => {
        if (!this.stopping && !this.cleanEnded) this.o.onState?.("error", "connection_lost");
        this.teardown();
      };
      this.node.port.onmessage = (e) => this._onFrame(e.data);
    }

    _send(int16, tsMs, gap) {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      this.ws.send(encodeFrame(this.seq++, tsMs, int16, gap));
    }

    _onFrame(int16) {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      const tsMs = this.total / 16; // real elapsed time (counts silence)
      this.total += int16.length;
      const db = rmsDb(int16);
      this.o.onLevel?.(Math.max(0, Math.min(1, (db + 60) / 60))); // -60..0 dBFS -> 0..1
      if (!this.silenceOn) { this._send(int16, tsMs, false); return; }
      // Silence-suspend (extension parity): drop sustained silence, resume with a short pre-roll.
      if (db >= SILENCE_DB) {
        if (this.suspended) {
          let first = true;
          for (const f of this.preroll) { this._send(f.int16, f.tsMs, first); first = false; }
          this.suspended = false; this.preroll = [];
          this._send(int16, tsMs, first);
        } else this._send(int16, tsMs, false);
        this.silentRun = 0;
      } else {
        this.silentRun++;
        if (this.suspended) {
          this.preroll.push({ tsMs, int16 });
          if (this.preroll.length > PREROLL_FRAMES) this.preroll.shift();
          if (this.silentRun % KEEPALIVE_FRAMES === 0) this._send(int16, tsMs, true);
        } else if (this.silentRun > HANGOVER_FRAMES) {
          this.suspended = true; this.preroll = [{ tsMs, int16 }];
        } else this._send(int16, tsMs, false);
      }
    }

    async _acquireWakeLock() {
      try {
        if (navigator.wakeLock?.request) {
          this.wakeLock = await navigator.wakeLock.request("screen");
          // Re-acquire after the page is hidden/shown (the lock releases on hide).
          this._visHandler = async () => {
            if (document.visibilityState === "visible" && !this.ended && navigator.wakeLock?.request) {
              try { this.wakeLock = await navigator.wakeLock.request("screen"); } catch { /* ignore */ }
            }
          };
          document.addEventListener("visibilitychange", this._visHandler);
        }
      } catch { /* unsupported / denied — no-op */ }
    }

    stop() {
      this.stopping = true;
      if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "end" }));
      else this.teardown();
    }

    teardown() {
      if (this.ended) return;
      this.ended = true;
      try { if (this._visHandler) document.removeEventListener("visibilitychange", this._visHandler); } catch {}
      try { this.wakeLock?.release(); } catch {} this.wakeLock = null;
      try { this.node?.disconnect(); } catch {}
      try { this.srcNode?.disconnect(); } catch {}
      try { this.stream?.getTracks().forEach((t) => t.stop()); } catch {}
      try { this.ws?.close(); } catch {}
      try { this.ctx?.close(); } catch {}
      this.ws = this.node = this.srcNode = this.stream = this.ctx = null;
    }
  }

  window.MinutesRecorder = Recorder;
})();
