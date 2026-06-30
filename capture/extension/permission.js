// Microphone setup page (a stable extension page — survives the native permission prompt, unlike
// the popup, which closes when focus leaves it). Drives four states — grant / setup / denied /
// no-device — lets the user pick an input and test its level, sets echo-cancellation, and persists
// {micGranted, micEnabled, micDeviceId, micDeviceLabel, micAec}. The offscreen reuses the granted
// same-origin permission when it captures the mic source.

const card = document.getElementById("card");
const BAR_COUNT = 16;

const MIC_SVG = `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="9" y="2.5" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/></svg>`;
const WARN_SVG = `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 9.5v5M12 17.5h.01"/></svg>`;

let stream = null;
let audioCtx = null;
let srcNode = null;
let analyser = null;
let raf = 0;

function stopStream() {
  if (raf) cancelAnimationFrame(raf), (raf = 0);
  try { if (srcNode) srcNode.disconnect(); } catch {}
  try { if (stream) stream.getTracks().forEach((t) => t.stop()); } catch {}
  srcNode = null;
  stream = null;
}

// ---------- GRANT ----------
function renderGrant() {
  stopStream();
  card.innerHTML = `
    <div class="perm__ic perm__ic--mic">${MIC_SVG}</div>
    <h2>Capture your own voice</h2>
    <p>Turn on <b>Host mic</b> to record your side of the call on a separate track — no second bot
      account, no extra participant. Your mic stays off until you allow it here.</p>
    <button class="fs-btn fs-btn--primary fs-btn--lg" id="grantbtn"><span class="src-mark src-mark--mic" style="margin-right:4px"></span>Allow microphone access</button>
    <div class="err" id="err" style="display:none"></div>
    <p class="center-note">Chrome will ask next — this page stays open so the prompt can't dismiss it.</p>`;
  document.getElementById("grantbtn").onclick = onGrant;
}

// ---------- SETUP (after grant) ----------
function lvlBars() {
  return Array.from({ length: BAR_COUNT }, () => `<i></i>`).join("");
}
async function renderSetup() {
  card.innerHTML = `
    <h2>Microphone ready</h2>
    <p style="margin-bottom:20px">Pick your device and confirm the level, then save.</p>
    <div class="perm-field">
      <label class="fs-label">Input device</label>
      <select class="fs-select" id="dev"></select>
    </div>
    <div class="perm-field">
      <label class="fs-label">Level — speak to confirm it's the right mic</label>
      <div class="lvl-wrap">
        <div class="lvl" id="meter">${lvlBars()}</div>
        <div class="lvl-wrap__cap"><span>input level</span><span id="levelcap">speak to test…</span></div>
      </div>
    </div>
    <div class="perm-toggle-row">
      <div><div class="perm-toggle-row__t">Echo cancellation</div><div class="perm-toggle-row__d">Leave off if you're on headphones — it can clip your voice. Default off.</div></div>
      <label class="fs-switch" id="aec"><span class="fs-switch__track"><span class="fs-switch__thumb"></span></span></label>
    </div>
    <div class="perm__actions">
      <button class="fs-btn" id="cancel">Cancel</button>
      <button class="fs-btn fs-btn--primary" id="save">Save &amp; close</button>
    </div>`;
  const saved = await chrome.storage.local.get(["micDeviceId", "micAec"]);
  if (saved.micAec) document.getElementById("aec").classList.add("is-on");
  if (!(await populateDevices(saved.micDeviceId))) return; // no devices -> no-device card already shown
  document.getElementById("dev").onchange = reacquire;
  document.getElementById("aec").onclick = () => { document.getElementById("aec").classList.toggle("is-on"); reacquire(); };
  document.getElementById("cancel").onclick = () => { stopStream(); window.close(); };
  document.getElementById("save").onclick = onSave;
  await reacquire();
}

function aecOn() { return document.getElementById("aec").classList.contains("is-on"); }

async function reacquire() {
  const sel = document.getElementById("dev");
  try { await acquire(sel.value || null, aecOn()); }
  catch (err) { classifyAndRender(err); }
}

async function onSave() {
  const sel = document.getElementById("dev");
  await chrome.storage.local.set({
    micGranted: true,
    micEnabled: true,
    micDeviceId: sel.value || null,
    micDeviceLabel: (sel.options[sel.selectedIndex]?.textContent || "").trim() || null,
    micAec: aecOn(),
  });
  stopStream();
  window.close();
}

// ---------- DENIED / BLOCKED ----------
function renderDenied() {
  stopStream();
  card.innerHTML = `
    <div class="perm__ic perm__ic--warn">${WARN_SVG}</div>
    <h2>Microphone is blocked</h2>
    <p>Chrome is blocking mic access for this extension, so Host mic can't be captured. Re-enable it,
      then come back and try again.</p>
    <div class="steps">1 · Open <code>chrome://settings/content/microphone</code><br/>2 · Move minutes to <b>Allowed</b><br/>3 · Reload this page</div>
    <div class="row">
      <button class="fs-btn" id="opensettings">Open Chrome settings</button>
      <button class="fs-btn fs-btn--primary" id="retry">↻ Try again</button>
    </div>
    <p class="center-note">You can still record <b>Online stream</b> without the mic.</p>`;
  document.getElementById("opensettings").onclick = () => chrome.tabs.create({ url: "chrome://settings/content/microphone" });
  document.getElementById("retry").onclick = onGrant;
}

// ---------- NO DEVICE ----------
function renderNoDevice() {
  stopStream();
  card.innerHTML = `
    <div class="perm__ic perm__ic--warn">${WARN_SVG}</div>
    <h2>No microphone found</h2>
    <p>Access is allowed, but Chrome doesn't see an input device — it may be unplugged, in use by
      another app, or disabled in your OS sound settings.</p>
    <div class="perm-field">
      <label class="fs-label">Input device</label>
      <select class="fs-select" disabled><option>No devices available</option></select>
    </div>
    <div class="row" style="margin-top:6px">
      <button class="fs-btn fs-btn--primary" id="rescan">↻ Re-scan devices</button>
      <button class="fs-btn" id="skip">Continue without mic</button>
    </div>`;
  document.getElementById("rescan").onclick = onGrant;
  document.getElementById("skip").onclick = () => { stopStream(); window.close(); };
}

// ---------- shared helpers ----------
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
  analyser.fftSize = 64; // 32 frequency bins — use the lower 16 (voice energy) across the bars
  srcNode.connect(analyser); // analyser only — NEVER to destination (no playback / echo)
  const bins = new Uint8Array(analyser.frequencyBinCount);
  const bars = [...card.querySelectorAll(".lvl i")];
  const cap = document.getElementById("levelcap");
  const tick = () => {
    analyser.getByteFrequencyData(bins);
    let sum = 0;
    bars.forEach((b, i) => {
      const v = bins[i] / 255; // 0..1
      sum += v;
      b.style.height = Math.max(8, Math.round(v * 100)) + "%";
      b.classList.toggle("is-on", v > 0.08);
    });
    if (cap) {
      const active = sum / bars.length > 0.04;
      cap.textContent = active ? "● picking up audio" : "speak to test…";
      cap.style.color = active ? "var(--ok)" : "var(--muted)";
    }
    raf = requestAnimationFrame(tick);
  };
  tick();
}

async function populateDevices(selectedId) {
  const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  if (!devs.length) { renderNoDevice(); return false; }
  const sel = document.getElementById("dev");
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
  return true;
}

function classifyAndRender(err) {
  const name = err && err.name;
  if (name === "NotAllowedError" || name === "SecurityError") return renderDenied();
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") return renderNoDevice();
  // Unknown failure — show it on the grant screen so the user can retry.
  renderGrant();
  const e = document.getElementById("err");
  if (e) { e.textContent = "Could not open the microphone: " + (err && err.message ? err.message : err); e.style.display = "block"; }
}

async function onGrant() {
  const saved = await chrome.storage.local.get(["micDeviceId", "micAec"]);
  try {
    await acquire(saved.micDeviceId, saved.micAec);
  } catch (err) {
    classifyAndRender(err);
    return;
  }
  await chrome.storage.local.set({ micGranted: true });
  await renderSetup();
}

// Boot: if the page already holds a mic permission, jump straight to setup; else ask to grant.
(async () => {
  let state = "prompt";
  try { state = (await navigator.permissions.query({ name: "microphone" })).state; } catch {}
  if (state === "granted") {
    const saved = await chrome.storage.local.get(["micDeviceId", "micAec"]);
    try { await acquire(saved.micDeviceId, saved.micAec); await chrome.storage.local.set({ micGranted: true }); await renderSetup(); }
    catch (err) { classifyAndRender(err); }
  } else if (state === "denied") {
    renderDenied();
  } else {
    renderGrant();
  }
})();

window.addEventListener("pagehide", stopStream);
window.addEventListener("beforeunload", stopStream);
