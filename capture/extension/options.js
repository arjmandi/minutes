// Extension settings — the single settings home: the minutes server URL AND the Host-mic setup
// (permission, device, live level test, echo cancellation) AND the silence-suspend cost saver.
// Available anytime (not just before recording). The offscreen reuses the granted same-origin mic
// permission; the popup reads micEnabled/micDeviceId/micAec/silenceSuspend from here.
const $ = (id) => document.getElementById(id);

/* =========================== SERVER =========================== */
function norm(u) {
  let s = (u || "").trim();
  if (!s) return "";
  if (!/^https?:\/\//i.test(s)) s = "https://" + s;
  try { return new URL(s).origin; } catch { return ""; }
}
function chip(kind, label) {
  const ic = { ok: "✓", warn: "!", err: "✕" }[kind];
  $("result").innerHTML = `<div class="chip ${kind}"><b>${ic}</b><span></span></div>`;
  $("result").querySelector("span").textContent = label; // untrusted server/error text
}
async function loadServer() {
  const st = await chrome.storage.local.get(["backendBase"]);
  $("server").value = st.backendBase || "";
}
async function saveServer() {
  const url = norm($("server").value);
  if (!/^https?:\/\/.+/.test(url)) { chip("err", "Enter a full URL, e.g. https://minutes.your-company.com"); return; }
  await chrome.storage.local.set({ backendBase: url });
  $("server").value = url;
  chip("ok", "Saved. You can sign in from the toolbar popup now.");
}
async function testServer() {
  const url = norm($("server").value);
  if (!/^https?:\/\/.+/.test(url)) { chip("err", "Enter a full URL, e.g. https://minutes.your-company.com"); return; }
  await chrome.storage.local.set({ backendBase: url });
  $("server").value = url;
  $("test").disabled = true; const prev = $("test").textContent; $("test").textContent = "Testing…";
  try {
    const r = await fetch(url + "/healthz", { method: "GET" });
    if (!r.ok) throw new Error("status " + r.status);
    const { deviceToken, deviceEmail } = await chrome.storage.local.get(["deviceToken", "deviceEmail"]);
    if (deviceToken) chip("ok", `Connected to ${url} · signed in as ${deviceEmail || "your account"}`);
    else chip("warn", `Saved + reachable: ${url}. Open the popup to sign in.`);
  } catch { chip("err", `Can't reach ${url} — check the address and that the server is up.`); }
  finally { $("test").disabled = false; $("test").textContent = prev; }
}
$("save").onclick = saveServer;
$("test").onclick = testServer;
$("server").addEventListener("keydown", (e) => { if (e.key === "Enter") saveServer(); });

/* ========================= MICROPHONE ========================= */
const micEl = $("mic");
const MIC_SVG = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="9" y="2.5" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/></svg>`;
const WARN_SVG = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 9.5v5M12 17.5h.01"/></svg>`;
const BAR_COUNT = 16;
let stream = null, audioCtx = null, srcNode = null, analyser = null, raf = 0;

function stopStream() {
  if (raf) cancelAnimationFrame(raf), (raf = 0);
  try { if (srcNode) srcNode.disconnect(); } catch {}
  try { if (stream) stream.getTracks().forEach((t) => t.stop()); } catch {}
  srcNode = null; stream = null;
}
async function acquire(deviceId, aec) {
  stopStream();
  const audio = { echoCancellation: !!aec, noiseSuppression: true, autoGainControl: true };
  if (deviceId) audio.deviceId = { exact: deviceId };
  stream = await navigator.mediaDevices.getUserMedia({ audio });
  startMeter();
}
function startMeter() {
  if (!audioCtx) audioCtx = new AudioContext();
  srcNode = audioCtx.createMediaStreamSource(stream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 64;
  srcNode.connect(analyser); // analyser only — never to destination (no playback / echo)
  const bins = new Uint8Array(analyser.frequencyBinCount);
  const bars = [...micEl.querySelectorAll(".lvl i")];
  const cap = $("levelcap");
  const tick = () => {
    analyser.getByteFrequencyData(bins);
    let sum = 0;
    bars.forEach((b, i) => { const v = bins[i] / 255; sum += v; b.style.height = Math.max(8, Math.round(v * 100)) + "%"; b.classList.toggle("is-on", v > 0.08); });
    if (cap) { const on = sum / bars.length > 0.04; cap.textContent = on ? "● picking up audio" : "speak to test…"; cap.style.color = on ? "var(--ok)" : "var(--muted)"; }
    raf = requestAnimationFrame(tick);
  };
  tick();
}
const sw = (on) => `<span class="fs-switch ${on ? "is-on" : ""}"><span class="fs-switch__track"><span class="fs-switch__thumb"></span></span></span>`;
const wireSwitch = (el, key, after) => {
  if (!el) return;
  el.onclick = async () => { el.classList.toggle("is-on"); await chrome.storage.local.set({ [key]: el.classList.contains("is-on") }); if (after) after(); };
};

async function renderMic() {
  stopStream();
  let state = "prompt";
  try { state = (await navigator.permissions.query({ name: "microphone" })).state; } catch {}
  const st = await chrome.storage.local.get(["micEnabled", "micDeviceId", "micDeviceLabel", "micAec", "silenceSuspend"]);

  if (state === "denied") {
    micEl.innerHTML = `<div class="ic ic--warn">${WARN_SVG}</div>
      <div style="font-size:15px;font-weight:600;margin-bottom:6px">Microphone is blocked</div>
      <div class="row2__d" style="max-width:none;margin-bottom:4px">Chrome is blocking mic access for this extension. Re-enable it, then re-check.</div>
      <div class="steps">1 · Open <code>chrome://settings/content/microphone</code><br/>2 · Move minutes to <b>Allowed</b><br/>3 · Re-check below</div>
      <div class="field"><button class="ghost" id="opensettings">Open Chrome settings</button><button class="primary" id="recheck">↻ Re-check</button></div>`;
    $("opensettings").onclick = () => chrome.tabs.create({ url: "chrome://settings/content/microphone" });
    $("recheck").onclick = renderMic;
    return;
  }
  if (state !== "granted") {
    micEl.innerHTML = `<div class="ic">${MIC_SVG}</div>
      <div style="font-size:15px;font-weight:600;margin-bottom:6px">Capture your own voice</div>
      <div class="row2__d" style="max-width:none;margin-bottom:14px">Allow microphone access to record a separate <b>Host mic</b> track — no second bot account in the call.</div>
      <button class="primary lg" id="grant"><span class="src-mark src-mark--mic" style="margin-right:4px"></span>Allow microphone access</button>
      <div class="hint">Chrome will ask next — this tab stays open so the prompt can't dismiss it.</div>`;
    $("grant").onclick = onGrant;
    return;
  }

  // granted — full settings
  const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  const opts = [`<option value="">Default microphone</option>`]
    .concat(devs.map((d) => `<option value="${d.deviceId}">${(d.label || "Microphone").replace(/</g, "")}</option>`))
    .join("");
  const micOn = !!st.micEnabled, aec = !!st.micAec, silence = st.silenceSuspend !== false;
  micEl.innerHTML = `
    <div class="row2" style="border-top:none;padding-top:0">
      <div><div class="row2__t"><span class="src-mark src-mark--mic"></span>Capture my microphone</div>
        <div class="row2__d">Add the Host-mic track on your next recording. You can also toggle this from the popup.</div></div>
      <span id="micen">${sw(micOn)}</span></div>
    <div class="field2" style="margin-top:4px"><label>Input device</label><select id="dev">${opts}</select></div>
    <div class="field2"><label>Level — speak to confirm it's the right mic</label>
      <div class="lvl-wrap"><div class="lvl">${Array.from({ length: BAR_COUNT }, () => "<i></i>").join("")}</div>
        <div class="lvl-wrap__cap"><span>input level</span><span id="levelcap">speak to test…</span></div></div></div>
    <div class="row2">
      <div><div class="row2__t">Echo cancellation</div><div class="row2__d">Leave off if you're on headphones — it can clip your voice. Default off.</div></div>
      <span id="aec">${sw(aec)}</span></div>
    <div class="row2">
      <div><div class="row2__t">Silence-suspend <span class="badge">cost saver</span></div><div class="row2__d">Stop streaming the Host mic during sustained silence and resume when you speak — fewer billed Soniox seconds. Default on. Turn off if quiet speech gets dropped.</div></div>
      <span id="silence">${sw(silence)}</span></div>`;

  const dev = $("dev");
  dev.value = st.micDeviceId || "";
  if (dev.value !== (st.micDeviceId || "")) dev.value = "";
  const aecOn = () => $("aec").querySelector(".fs-switch").classList.contains("is-on");
  const reacquire = async () => { try { await acquire(dev.value || null, aecOn()); } catch { renderMic(); } };
  dev.onchange = async () => {
    await chrome.storage.local.set({ micDeviceId: dev.value || null, micDeviceLabel: (dev.options[dev.selectedIndex]?.textContent || "").trim() || null });
    reacquire();
  };
  wireSwitch($("micen").querySelector(".fs-switch"), "micEnabled");
  wireSwitch($("aec").querySelector(".fs-switch"), "micAec", reacquire);
  wireSwitch($("silence").querySelector(".fs-switch"), "silenceSuspend");

  await chrome.storage.local.set({ micGranted: true, micDeviceLabel: (dev.options[dev.selectedIndex]?.textContent || "").trim() || st.micDeviceLabel || null });
  reacquire();
}
async function onGrant() {
  try { await acquire(null, false); await chrome.storage.local.set({ micGranted: true, micEnabled: true }); }
  catch { /* denied/no-device — renderMic re-reads the permission state and shows the right card */ }
  renderMic();
}
window.addEventListener("pagehide", stopStream);

loadServer();
renderMic();
