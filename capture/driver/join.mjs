// Bot-join driver: launch Chrome with the capture extension + a persistent profile (so the bot
// account stays signed in across runs), join a Meet/Teams meeting, and trigger capture for that
// tab via the content script. Headed (loading an extension + tab audio needs headed Chromium).
//
//   MINUTES_CAPTURE_TOKEN=<token> node join.mjs <meeting-url>
//   env: MINUTES_BACKEND_URL (default ws://localhost:8000/ingest)
//
// NOTE: join selectors and the login flow need live tuning per platform — Google/Microsoft change
// their pre-join UIs. The launch + extension + profile + capture-trigger plumbing is the stable
// part; the driver always attempts to start capture after a settle window even if auto-join misses
// a selector (so you can complete the join by hand).
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const EXT = path.resolve(__dirname, "..", "extension");

const MEETING_URL = process.argv[2] || process.env.MINUTES_MEETING_URL;
const BACKEND = process.env.MINUTES_BACKEND_URL || "ws://localhost:8000/ingest";
const TOKEN = process.env.MINUTES_CAPTURE_TOKEN || "";

if (!MEETING_URL) {
  console.error("usage: node join.mjs <meeting-url>   (env: MINUTES_CAPTURE_TOKEN, MINUTES_BACKEND_URL)");
  process.exit(1);
}
if (!TOKEN) {
  console.error("set MINUTES_CAPTURE_TOKEN (mint one via POST /auth/dev-token)");
  process.exit(1);
}

function detect(url) {
  const u = new URL(url);
  if (u.hostname === "meet.google.com") {
    return { platform: "meet", externalMeetingId: (u.pathname.match(/([a-z]{3}-[a-z]{4}-[a-z]{3})/) || [])[1] || "" };
  }
  if (u.hostname === "teams.microsoft.com") {
    return { platform: "teams", externalMeetingId: (decodeURIComponent(u.href).match(/(19:meeting_[^@]+@thread\.v2)/) || [])[1] || "" };
  }
  return { platform: "meet", externalMeetingId: "" };
}

const meeting = detect(MEETING_URL);
const profileDir = path.resolve(__dirname, ".profiles", meeting.platform);

const context = await chromium.launchPersistentContext(profileDir, {
  headless: false, // extension + tab audio require headed Chromium
  args: [
    `--disable-extensions-except=${EXT}`,
    `--load-extension=${EXT}`,
    "--use-fake-ui-for-media-stream", // auto-accept mic/cam permission prompts
    "--autoplay-policy=no-user-gesture-required",
  ],
});

const page = await context.newPage();
console.log(`[driver] opening ${meeting.platform} meeting ${meeting.externalMeetingId || MEETING_URL}`);
await page.goto(MEETING_URL, { waitUntil: "domcontentloaded" });

// Sign-in: the persistent profile reuses the bot-account login; the first run needs a manual login.
if (/accounts\.google\.com|login\.microsoftonline\.com|login\.live\.com/.test(page.url())) {
  console.log("[driver] LOGIN REQUIRED — sign the bot account in via the opened window (persists to the profile). Waiting up to 5 min…");
  await page.waitForURL((u) => /meet\.google\.com|teams\.microsoft\.com/.test(u.href), { timeout: 300000 });
  console.log("[driver] login complete.");
}

async function clickByText(re, timeout = 30000) {
  const btn = page.getByRole("button", { name: re }).first();
  await btn.waitFor({ timeout });
  await btn.click();
}

// Join (best-effort; selectors need live tuning).
try {
  if (meeting.platform === "meet") {
    for (const t of [/got it/i, /dismiss/i]) {
      try { await page.getByRole("button", { name: t }).first().click({ timeout: 3000 }); } catch {}
    }
    await clickByText(/join now|ask to join/i, 30000);
  } else {
    try { await clickByText(/continue on this browser/i, 8000); } catch {}
    await clickByText(/join now/i, 30000);
  }
  console.log("[driver] join clicked.");
} catch (err) {
  console.log(`[driver] auto-join did not find a button (${err.message}); complete the join manually — capture starts in 20s regardless.`);
}

// Settle (and clear the lobby if the host must admit), then start capture for this tab.
await page.waitForTimeout(20000);
await page.evaluate(
  (cfg) => window.postMessage({ source: "minutes-driver", type: "start", config: cfg }, "*"),
  {
    backendUrl: BACKEND,
    token: TOKEN,
    platform: meeting.platform,
    externalMeetingId: meeting.externalMeetingId,
    callId: crypto.randomUUID(),
  }
);
console.log("[driver] capture started. Press Ctrl-C to stop.");

async function shutdown() {
  console.log("\n[driver] stopping capture…");
  try {
    await page.evaluate(() => window.postMessage({ source: "minutes-driver", type: "stop" }, "*"));
    await page.waitForTimeout(2000);
  } catch {}
  await context.close();
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

await new Promise(() => {}); // keep the meeting open until interrupted
