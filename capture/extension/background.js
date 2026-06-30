// Service worker: orchestrates dual-source tab+mic capture via one offscreen document.
// Tab capture: the SW mints a media-stream id for the target tab; the offscreen redeems it with
// getUserMedia (a SW has no DOM). Mic capture: the offscreen calls getUserMedia({audio}) directly.
// Each source runs its own pipeline + WebSocket in the offscreen; the SW just routes start/stop and
// reflects the live sources in the toolbar icon.

const OFFSCREEN_URL = "offscreen.html";

// Four-state toolbar icon: off / tab only ("Online stream") / mic only ("Host mic") / both.
const ICONS = {
  off: { 16: "icons/idle-16.png", 32: "icons/idle-32.png", 48: "icons/idle-48.png" },
  tab: { 16: "icons/tab-16.png", 32: "icons/tab-32.png", 48: "icons/tab-48.png" },
  mic: { 16: "icons/mic-16.png", 32: "icons/mic-32.png", 48: "icons/mic-48.png" },
  both: { 16: "icons/both-16.png", 32: "icons/both-32.png", 48: "icons/both-48.png" },
};
const TITLES = {
  off: "minutes",
  tab: "minutes — recording the tab",
  mic: "minutes — recording your mic",
  both: "minutes — recording tab + mic",
};
function pickIcon(sources) {
  const tab = !!sources?.tab?.capturing;
  const mic = !!sources?.mic?.capturing;
  if (tab && mic) return "both";
  if (mic) return "mic";
  if (tab) return "tab";
  return "off";
}
function setIconState(state) {
  try {
    chrome.action.setIcon({ path: ICONS[state] });
    chrome.action.setTitle({ title: TITLES[state] });
  } catch { /* action API unavailable */ }
}

// On (re)load, reflect reality: an offscreen doc exists iff some capture is alive. Clear a stale
// flag if not; otherwise show the icon for whatever sources storage last reported.
(async () => {
  try {
    if (!(await chrome.offscreen.hasDocument?.())) {
      await chrome.storage.local.set({ minutesCapture: { sources: {} } });
      setIconState("off");
    } else {
      const st = await chrome.storage.local.get("minutesCapture");
      setIconState(pickIcon(st.minutesCapture?.sources));
    }
  } catch { /* ignore */ }
})();

// Keep the toolbar icon in sync with the per-source capture flags the offscreen maintains.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.minutesCapture) {
    setIconState(pickIcon(changes.minutesCapture.newValue?.sources));
  }
});

async function ensureOffscreen() {
  if (await chrome.offscreen.hasDocument?.()) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ["USER_MEDIA"],
    justification: "Capture tab + microphone audio for live transcription.",
  });
}

async function startTab(tabId, config) {
  // Fresh tab capture: reset a stale offscreen (e.g. from a crash) so getMediaStreamId doesn't throw
  // "Cannot capture a tab with an active stream". A normal Stop already closed the doc; the mic (if
  // enabled) is added AFTER this, so this never tears down a live mic.
  try {
    if (await chrome.offscreen.hasDocument?.()) await chrome.offscreen.closeDocument();
  } catch {}
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await ensureOffscreen();
  await chrome.runtime.sendMessage({
    target: "offscreen", type: "start", source: "tab", streamId, config,
  });
}

async function addMic(config, deviceId, aec) {
  // Mic needs no tabCapture + must NOT close the doc (that would kill a live tab capture).
  await ensureOffscreen();
  await chrome.runtime.sendMessage({
    target: "offscreen", type: "add-source", source: "mic", deviceId, aec, config,
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "start") {
        await startTab(msg.tabId, msg.config); // from the popup (explicit tabId)
        sendResponse({ ok: true });
      } else if (msg.type === "start-here") {
        await startTab(sender.tab?.id, msg.config); // from the content script (own tab)
        sendResponse({ ok: true });
      } else if (msg.type === "add-mic") {
        await addMic(msg.config, msg.deviceId, msg.aec);
        sendResponse({ ok: true });
      } else if (msg.type === "remove-source") {
        await chrome.runtime
          .sendMessage({ target: "offscreen", type: "remove-source", source: msg.source })
          .catch(() => {});
        sendResponse({ ok: true });
      } else if (msg.type === "stop") {
        await chrome.runtime
          .sendMessage({ target: "offscreen", type: "stop" })
          .catch(() => {});
        sendResponse({ ok: true });
      } else if (msg.type === "capture-status") {
        // Direct push from the offscreen on every status change -> drive the toolbar icon. More
        // reliable than waiting on storage.onChanged to wake this worker.
        setIconState(pickIcon(msg.sources));
        sendResponse({ ok: true });
      } else if (msg.type === "capture-state") {
        // Authoritative liveness for the popup: the offscreen doc exists iff a capture is alive.
        const active = !!(await chrome.offscreen.hasDocument?.());
        let sources = {};
        if (active) {
          const st = await chrome.storage.local.get("minutesCapture");
          const s = st.minutesCapture?.sources || {};
          sources = { tab: !!s.tab?.capturing, mic: !!s.mic?.capturing };
        }
        sendResponse({ active, sources });
      } else if (msg.type === "capture-ended") {
        // All sources ended in the offscreen -> close the doc + drop the icon.
        try {
          if (await chrome.offscreen.hasDocument?.()) await chrome.offscreen.closeDocument();
        } catch {}
        setIconState("off");
        sendResponse({ ok: true });
      }
    } catch (err) {
      sendResponse({ ok: false, error: String(err) });
    }
  })();
  return true; // async sendResponse
});
