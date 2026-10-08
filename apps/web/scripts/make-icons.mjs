// Renders the ClaimPilot mark (src/app/icon.svg) to the PNG icons a PWA needs:
//
//   node scripts/make-icons.mjs
//
// Writes public/icons/icon-192.png, icon-512.png, maskable-512.png and src/app/apple-icon.png.
// Uses the same browser Playwright uses for the E2E tests (installed Edge/Chrome, or chromium
// with PW_CHANNEL=chromium). Run it again only when the logo changes; the PNGs are committed.
import { chromium } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const svg = readFileSync(`${root}src/app/icon.svg`, "utf8");
const edge = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].some((path) => existsSync(path));
const channel =
  process.env.PW_CHANNEL === "chromium"
    ? undefined
    : (process.env.PW_CHANNEL ?? (edge ? "msedge" : "chrome"));

mkdirSync(`${root}public/icons`, { recursive: true });

const browser = await chromium.launch({ channel });
try {
  const page = await browser.newPage();

  /** The mark centred on a square; `padding` leaves the "safe zone" maskable icons need. */
  async function render(size, path, { padding = 0, background = "transparent" } = {}) {
    const inner = Math.round(size * (1 - padding * 2));
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(
      `<body style="margin:0;display:grid;place-items:center;width:${size}px;height:${size}px;background:${background}">` +
        `<div style="width:${inner}px;height:${inner}px">${svg.replace("<svg ", `<svg width="${inner}" height="${inner}" `)}</div></body>`,
    );
    await page.screenshot({ path, omitBackground: background === "transparent" });
    console.log(`wrote ${path}`);
  }

  await render(192, `${root}public/icons/icon-192.png`);
  await render(512, `${root}public/icons/icon-512.png`);
  await render(512, `${root}public/icons/maskable-512.png`, {
    padding: 0.12,
    background: "#4f46e5",
  });
  await render(180, `${root}src/app/apple-icon.png`, { padding: 0.08, background: "#ffffff" });
} finally {
  await browser.close();
}
