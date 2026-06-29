// Extension settings: configure the minutes server URL (domain or http://IP:port). The popup signs
// in against this server. "Test connection" probes /healthz and reports reachable/signed-in state.
const $ = (id) => document.getElementById(id);

// Reduce whatever the user pastes to a clean ORIGIN (scheme://host[:port]) — strips any path like
// "/app" or "/api" and adds https:// if missing. This guarantees the popup builds /api/... against
// the right base (a stray path was sending POSTs to the static landing -> HTTP 405).
function norm(u) {
  let s = (u || "").trim();
  if (!s) return "";
  if (!/^https?:\/\//i.test(s)) s = "https://" + s;
  try { return new URL(s).origin; } catch { return ""; }
}

function chip(kind, label) {
  const ic = { ok: "✓", warn: "!", err: "✕" }[kind];
  $("result").innerHTML = `<div class="chip ${kind}"><b>${ic}</b><span></span></div>`;
  $("result").querySelector("span").textContent = label; // textContent — server/error text is untrusted
}

async function load() {
  const st = await chrome.storage.local.get(["backendBase"]);
  $("server").value = st.backendBase || "";
}

async function save() {
  const url = norm($("server").value);
  if (!/^https?:\/\/.+/.test(url)) {
    chip("err", "Enter a full URL, e.g. https://minutes.your-company.com");
    return;
  }
  await chrome.storage.local.set({ backendBase: url });
  $("server").value = url;
  chip("ok", "Saved. You can sign in from the toolbar popup now.");
}

async function test() {
  const url = norm($("server").value);
  if (!/^https?:\/\/.+/.test(url)) {
    chip("err", "Enter a full URL, e.g. https://minutes.your-company.com");
    return;
  }
  // Persist the URL we're about to test, so the popup signs in against the SAME server (no
  // "tested one URL, logged in against another" trap).
  await chrome.storage.local.set({ backendBase: url });
  $("server").value = url;
  $("test").disabled = true;
  const prev = $("test").textContent;
  $("test").textContent = "Testing…";
  try {
    const r = await fetch(url + "/healthz", { method: "GET" });
    if (!r.ok) throw new Error("status " + r.status);
    const { deviceToken, deviceEmail } = await chrome.storage.local.get(["deviceToken", "deviceEmail"]);
    if (deviceToken) chip("ok", `Connected to ${url} · signed in as ${deviceEmail || "your account"}`);
    else chip("warn", `Saved + reachable: ${url}. Open the popup to sign in.`);
  } catch {
    chip("err", `Can't reach ${url} — check the address and that the server is up.`);
  } finally {
    $("test").disabled = false;
    $("test").textContent = prev;
  }
}

$("save").onclick = save;
$("test").onclick = test;
$("server").addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
load();
