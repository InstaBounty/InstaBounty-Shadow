"use strict";
// Standalone browser launcher: each role has a distinct WebKit user data dir.
// Persistent context keeps local storage and cookies only in that profile.
const path = require("node:path");
const { webkit, devices } = require("C:/InstaBounty/browser-testing/node_modules/playwright");

async function main() {
  const [profile, url] = process.argv.slice(2);
  if (!profile || !url || !path.isAbsolute(profile)) {
    throw new Error("Expected absolute profile directory and startup URL");
  }
  if (!(url === "about:blank" || /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?(?:\/|$)/i.test(url))) {
    throw new Error("Only local InstaBounty URLs or about:blank are supported");
  }
  const context = await webkit.launchPersistentContext(profile, {
    ...devices["iPhone 13"],
    headless: false,
  });
  const page = context.pages()[0] || await context.newPage();
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(err => {
    process.stderr.write("WebKit page navigation failed: " + err.message + "\n");
  });
  console.log("WebKit iPhone-like user profile opened: " + profile);
  // Keep process alive while the visible persistent context is running.
  await new Promise(resolve => context.on("close", resolve));
}

main().catch(err => {
  console.error("Unable to launch WebKit test browser: " + err.message);
  process.exitCode = 1;
});
