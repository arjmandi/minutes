// Offscreen document: redeem the tab stream, run the audio pipeline, stream framed PCM to the
// backend ingest WS. Wire format matches app/audio/frames.py: 13-byte LE header
// (seq uint32, ts_ms uint64, flags uint8) + Int16 PCM (s16le). Auth via the
// `minutes.auth.bearer` subprotocol so the token never lands in the URL.

const AUTH_SUBPROTOCOL = "minutes.auth.bearer";

let ctx = null;
let ws = null;
let node = null;
let source = null;
let stream = null;
let seq = 0;
let totalSamples = 0;
let stopping = false;
let frames = 0;
let lastReportFrames = 0;
let ended = false; // guards cleanup so teardown + the capture-ended signal fire exactly once

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== "offscreen") return;
  if (msg.type === "start") start(msg.streamId, msg.config);
  else if (msg.type === "stop") stop();
});

// Push status to the popup (if open) AND persist it, so a re-opened popup reflects reality.
function setStatus(text, capturing) {
  chrome.runtime.sendMessage({ target: "popup", type: "status", status: text }).catch(() => {});
  try {
    chrome.storage.local.set({ minutesCapture: { capturing, status: text, at: Date.now() } });
  } catch {}
}

// RMS level of a frame in dBFS: ~ -90 = silence, ~ -40..-15 = speech. Lets you SEE if audio flows.
function rmsDb(int16) {
  let sum = 0;
  for (let i = 0; i < int16.length; i++) {
    const v = int16[i] / 32768;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / int16.length);
  return rms > 0 ? Math.round(20 * Math.log10(rms)) : -99;
}

function encodeFrame(seqNo, tsMs, int16) {
  const buf = new ArrayBuffer(13 + int16.byteLength);
  const dv = new DataView(buf);
  dv.setUint32(0, seqNo >>> 0, true);
  dv.setBigUint64(4, BigInt(Math.trunc(tsMs)), true);
  dv.setUint8(12, 0); // flags: bit0 = gap (unused for now)
  new Uint8Array(buf, 13).set(new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength));
  return buf;
}

async function start(streamId, config) {
  seq = 0;
  totalSamples = 0;
  frames = 0;
  lastReportFrames = 0;
  stopping = false;
  ended = false;
  setStatus("starting…", true); // mark capture live up front (popup also holds a startup grace)
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    });
  } catch (err) {
    setStatus("capture_failed: " + err, false);
    cleanup();
    return;
  }

  ctx = new AudioContext();
  await ctx.audioWorklet.addModule("pcm-worklet.js");
  source = ctx.createMediaStreamSource(stream);
  node = new AudioWorkletNode(ctx, "pcm-downsampler");
  source.connect(node);
  source.connect(ctx.destination); // passthrough so the human still hears the meeting

  ws = new WebSocket(config.backendUrl, [AUTH_SUBPROTOCOL, config.token]);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    setStatus("connected — waiting for admission…", true);
    ws.send(
      JSON.stringify({
        type: "hello",
        platform: config.platform,
        external_meeting_id: config.externalMeetingId,
        call_id: config.callId,
      })
    );
  };

  ws.onmessage = (ev) => {
    let data;
    try {
      data = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (data.type === "admitted") setStatus("capturing — 0 frames", true);
    else if (data.type === "ended") {
      setStatus("ended · " + data.segments + " segments", false);
      cleanup();
    } else if (["rejected", "conflict", "forbidden", "error"].includes(data.type)) {
      setStatus("denied · " + (data.reason || data.type), false);
      cleanup();
    }
  };

  ws.onerror = () => setStatus("ws_error", false);
  ws.onclose = () => {
    if (!stopping) setStatus("disconnected", false);
    cleanup();
  };

  node.port.onmessage = (e) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const int16 = e.data; // Int16Array, 1600 samples (100 ms @ 16 kHz)
    const tsMs = totalSamples / 16; // 16 samples per ms at 16 kHz
    totalSamples += int16.length;
    ws.send(encodeFrame(seq++, tsMs, int16));
    frames++;
    if (frames - lastReportFrames >= 10) {
      // ~once per second; the dBFS makes silent-tab vs real-audio obvious.
      lastReportFrames = frames;
      setStatus("capturing · " + frames + " frames · " + rmsDb(int16) + " dBFS", true);
    }
  };
}

function stop() {
  stopping = true;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end" })); // backend finalizes, replies {"type":"ended"}
  } else {
    setStatus("stopped", false);
    cleanup();
  }
}

function cleanup() {
  if (ended) return; // fire teardown + the capture-ended signal exactly once
  ended = true;
  try { if (node) node.disconnect(); } catch {}
  try { if (source) source.disconnect(); } catch {}
  try { if (stream) stream.getTracks().forEach((t) => t.stop()); } catch {}
  try { if (ctx) ctx.close(); } catch {}
  try { if (ws) ws.close(); } catch {}
  ctx = ws = node = source = stream = null;
  // Capture is over -> have the background close this offscreen doc (hasDocument()=false, icon idle).
  // The popup's authority is that doc's existence, so this is what flips the popup back to idle.
  chrome.runtime.sendMessage({ type: "capture-ended" }).catch(() => {});
}
