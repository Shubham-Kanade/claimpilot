// Renders docs/TECHNICAL_DESIGN.md to docs/TECHNICAL_DESIGN.pdf, with the Mermaid diagrams drawn.
//
//   cd docs/tools && npm install && npm run pdf
//
// Uses an installed Chromium-family browser through playwright-core: Edge (default on Windows),
// Chrome, or Playwright's own Chromium. Override with BROWSER_CHANNEL=chrome|msedge|chromium.
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import MarkdownIt from "markdown-it";
import { chromium } from "playwright-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const docs = path.resolve(here, "..");
const source = path.join(docs, "TECHNICAL_DESIGN.md");
const output = path.join(docs, "TECHNICAL_DESIGN.pdf");
const require = createRequire(import.meta.url);
const mermaidScript = require.resolve("mermaid/dist/mermaid.min.js");

const md = new MarkdownIt({ html: false, linkify: true, typographer: false });

// ```mermaid blocks become <pre class="mermaid"> for the mermaid library to draw.
const defaultFence = md.renderer.rules.fence;
md.renderer.rules.fence = (tokens, idx, options, env, self) => {
  const token = tokens[idx];
  if (token.info.trim() === "mermaid") {
    return `<pre class="mermaid">${md.utils.escapeHtml(token.content)}</pre>\n`;
  }
  return defaultFence(tokens, idx, options, env, self);
};

// Heading anchors, so the reading guide's section numbers can be followed in the PDF.
md.renderer.rules.heading_open = (tokens, idx, options, env, self) => {
  const title = tokens[idx + 1].content;
  const id = title
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/\s+/g, "-");
  tokens[idx].attrSet("id", id);
  return self.renderToken(tokens, idx, options);
};

const css = `
  @page { size: A4; margin: 16mm 14mm 18mm 14mm; }
  body { font: 10.5pt/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; color: #1b1f24; max-width: 100%; }
  h1 { font-size: 22pt; margin: 0 0 6pt; } h2 { font-size: 15pt; margin: 20pt 0 6pt; border-bottom: 1px solid #d0d7de; padding-bottom: 3pt; }
  h3 { font-size: 12pt; margin: 14pt 0 4pt; } h4 { font-size: 11pt; }
  h1, h2, h3 { break-after: avoid; }
  table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.2pt; break-inside: auto; }
  th, td { border: 1px solid #d0d7de; padding: 3pt 6pt; vertical-align: top; text-align: left; }
  th { background: #f3f5f8; } tr { break-inside: avoid; }
  code { font: 9pt Consolas, "Cascadia Mono", monospace; background: #f1f3f5; padding: 0 3pt; border-radius: 3pt; }
  pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6pt; padding: 8pt; overflow: hidden; white-space: pre-wrap; break-inside: avoid; }
  pre code { background: none; padding: 0; }
  pre.mermaid { background: #fff; border: none; text-align: center; white-space: normal; padding: 4pt 0; }
  pre.mermaid svg { max-width: 100%; height: auto; }
  blockquote { margin: 8pt 0; padding: 2pt 10pt; border-left: 3px solid #8b9bb4; color: #44505f; background: #f7f9fc; }
  a { color: #1f5fbf; text-decoration: none; }
`;

const markdown = await readFile(source, "utf8");
const body = md.render(markdown);
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>ClaimPilot: Technical Design Document</title>
<style>${css}</style></head><body>${body}
<script src="${pathToFileURL(mermaidScript).href}"></script>
<script>
  mermaid.initialize({ startOnLoad: false, theme: "default", securityLevel: "loose", flowchart: { htmlLabels: true } });
  window.__mermaidDone = mermaid.run({ querySelector: "pre.mermaid" }).then(() => true, (e) => { console.error(e); return false; });
</script></body></html>`;

const htmlPath = path.join(here, ".build.html");
await writeFile(htmlPath, html, "utf8");

const channel = process.env.BROWSER_CHANNEL ?? (process.platform === "win32" ? "msedge" : "chromium");
const browser = await chromium.launch({ channel: channel === "chromium" ? undefined : channel });
try {
  const page = await browser.newPage();
  page.on("console", (m) => m.type() === "error" && console.error("browser:", m.text()));
  await page.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
  const drawn = await page.evaluate(() => window.__mermaidDone);
  if (!drawn) throw new Error("a Mermaid diagram failed to render");
  await page.pdf({
    path: output,
    format: "A4",
    printBackground: true,
    displayHeaderFooter: true,
    headerTemplate: "<span></span>",
    footerTemplate:
      '<div style="font-size:8px;color:#6a737d;width:100%;text-align:center">ClaimPilot: Technical Design Document · <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
    margin: { top: "16mm", bottom: "18mm", left: "14mm", right: "14mm" },
  });
  console.log(`wrote ${path.relative(process.cwd(), output)}`);
} finally {
  await browser.close();
}
