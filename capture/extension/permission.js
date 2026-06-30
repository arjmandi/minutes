// Microphone setup page (a stable extension page — survives the native permission prompt, unlike
// the popup, which closes when focus leaves it). Grants mic access, lets the user pick an input and
// test its level, sets echo-cancellation, and persists {micGranted, micDeviceId, micAec}. The
// offscreen reuses the granted same-origin permission when it captures the mic source.

const $ = (id) => document.getElementById(id);
const BAR_COUNT = 7;

let stream = null;
let audioCtx = null;
let srcNode = null;
let analyser = null;
let raf = 0;

// Build the meter bars.
const meterEl = $("meter");
for (let i = 0; i < BAR_COUNT; i++) meterEl.appendChild(document.createElement("i"));
const bars = [...meterEl.querySelectorAll("i")];

function stopStream() {
  if (raf) cancelAnimationFrame(raf), (raf = 0);
  try { if (srcNode) srcNode.disconnect(); } catch {}
  try { if (stream) stream.getTracks().forEach((t) => t.stop()); } catch {}
  srcNode = null;
  stream = null;
}

function startMeter() {
  if (!audioCtx) audioCtx = new AudioContext();
  srcNode = audioCtx.createMediaStreamSource(stream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  srcNode.connect(analyser); // analyser only — NOT to destination (no playback / echo)
  const buf = new Float32Array(analyser.fftSize);
  const tick = () => {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    const db = rms > 0 ? 20 * Math.log10(rms) : -99; // same dBFS scale as the capture meter
    const level = Math.max(0, Math.min(1, (db + 60) / 60)); // -60 dBFS..0 -> 0..1
    const lit = Math.round(level * BAR_COUNT);
    bars.forEach((b, i) => {
      const on = i < lit;
      b.style.height = on ? 8 + ((i + 1) / BAR_COUNT) * 36 + "px" : "4px";
      b.style.background = on ? "var(--accent)" : "#d8d5cc";
    });
    raf = requestAnimationFrame(tick);
  };
  tick();
}

async function acquire(deviceId, aec) {
  stopStream();
  const audio = { echoCancellation: !!aec, noiseSuppression: true, autoGainControl: true };
  if (deviceId) audio.deviceId = { exact: deviceId };
  stream = await navigator.mediaDevices.getUserMedia({ audio });
  startMeter();
}

async function populateDevices(selectedId) {
  const devs = (await navigator.mediaDevices.enumerateDevices()).filter(
    (d) => d.kind === "audioinput"
  );
  const sel = $("dev");
  sel.innerHTML = "";
  const def = document.createElement("option");
  def.value = "";
  def.textContent = "Default microphone";
  sel.appendChild(def);
  for (const d of devs) {
    const o = document.createElement("option");
    o.value = d.deviceId;
    o.textContent = d.label || `Microphone ${sel.length}`;
    sel.appendChild(o);
  }
  sel.value = selectedId || "";
  if (sel.value !== (selectedId || "")) sel.value = ""; // stored device gone -> default
}

async function onGrant() {
  $("granterr").style.display = "none";
  const saved = await chrome.storage.local.get(["micDeviceId", "micAec"]);
  $("aec").checked = !!saved.micAec;
  try {
    await acquire(saved.micDeviceId, saved.micAec);
  } catch (err) {
    $("granterr").textContent =
      err && err.name === "NotAllowedError"
        ? "Microphone access was blocked. Allow it for this extension in chrome://settings, then retry."
        : "Could not open the microphone: " + (err && err.message ? err.message : err);
    $("granterr").style.display = "block";
    return;
  }
  await chrome.storage.local.set({ micGranted: true });
  await populateDevices(saved.micDeviceId);
  $("grant").style.display = "none";
  $("setup").style.display = "block";
}

$("grantbtn").onclick = onGrant;

$("dev").onchange = async () => {
  try { await acquire($("dev").value || null, $("aec").checked); } catch {}
};
$("aec").onchange = async () => {
  try { await acquire($("dev").value || null, $("aec").checked); } catch {}
};

$("save").onclick = async () => {
  await chrome.storage.local.set({
    micGranted: true,
    micEnabled: true,
    micDeviceId: $("dev").value || null,
    micAec: $("aec").checked,
  });
  stopStream();
  window.close();
};

$("cancel").onclick = () => {
  stopStream();
  window.close();
};

window.addEventListener("pagehide", stopStream);
window.addEventListener("beforeunload", stopStream);
