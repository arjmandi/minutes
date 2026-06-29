// Service worker: orchestrates tab-audio capture via an offscreen document.
// MV3 tab capture flow: the service worker mints a media-stream id for the target tab, then an
// offscreen document redeems it with getUserMedia (a service worker has no DOM/getUserMedia).

const OFFSCREEN_URL = "offscreen.html";

// Toolbar icon reflects capture state: the designed idle vs recording marks (the recording mark
// carries the red dot), so the icon itself is the indicator — no badge needed.
const ICONS = {
  idle: { 16: "icons/idle-16.png", 32: "icons/idle-32.png", 48: "icons/idle-48.png" },
  recording: { 16: "icons/rec-16.png", 32: "icons/rec-32.png", 48: "icons/rec-48.png" },
};
function setRecording(on) {
  try {
    chrome.action.setIcon({ path: on ? ICONS.recording : ICONS.idle });
    chrome.action.setTitle({ title: on ? "minutes — recording" : "minutes" });
  } catch { /* action API unavailable */ }
}

// On (re)load, clear a stale "capturing" flag unless an offscreen capture is genuinely still alive.
(async () => {
  try {
    if (!(await chrome.offscreen.hasDocument?.())) {
      await chrome.storage.local.set({ minutesCapture: { capturing: false, status: "" } });
      setRecording(false);
    } else {
      setRecording(true);
    }
  } catch { /* ignore */ }
})();

// Keep the badge in sync with the capture flag the offscreen doc maintains.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.minutesCapture) {
    setRecording(!!changes.minutesCapture.newValue?.capturing);
  }
});

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument?.();
  if (has) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ["USER_MEDIA"],
    justification: "Capture meeting tab audio for transcription.",
  });
}

async function startCapture(tabId, config) {
  // Reset any prior capture first, else getMediaStreamId throws "Cannot capture a tab with an
  // active stream" when the offscreen doc is still holding the previous tab stream.
  try {
    if (await chrome.offscreen.hasDocument?.()) await chrome.offscreen.closeDocument();
  } catch {}
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await ensureOffscreen();
  await chrome.runtime.sendMessage({ target: "offscreen", type: "start", streamId, config });
  setRecording(true); // the offscreen doc now exists = capture is live (see hasDocument authority)
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "start") {
        // From the popup: explicit tabId.
        await startCapture(msg.tabId, msg.config);
        sendResponse({ ok: true });
      } else if (msg.type === "start-here") {
        // From the content script (bot-join driver): capture the sender's own tab.
        await startCapture(sender.tab?.id, msg.config);
        sendResponse({ ok: true });
      } else if (msg.type === "stop") {
        await chrome.runtime.sendMessage({ target: "offscreen", type: "stop" }).catch(() => {});
        setRecording(false);
        sendResponse({ ok: true });
      } else if (msg.type === "capture-state") {
        // Authoritative "is capture live" for the popup: an offscreen doc exists iff we're capturing.
        sendResponse({ active: !!(await chrome.offscreen.hasDocument?.()) });
      } else if (msg.type === "capture-ended") {
        // Offscreen finished/failed -> close the doc so hasDocument() reflects reality, drop the icon.
        try {
          if (await chrome.offscreen.hasDocument?.()) await chrome.offscreen.closeDocument();
        } catch {}
        setRecording(false);
        sendResponse({ ok: true });
      }
    } catch (err) {
      sendResponse({ ok: false, error: String(err) });
    }
  })();
  return true; // async sendResponse
});
