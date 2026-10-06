#!/usr/bin/env node
// Export the same offline HTML in a fresh headless browser context.
import { createRequire } from 'node:module';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const require = createRequire(import.meta.url);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const suppliedModules = process.env.COLLECTION_NODE_MODULES;
let playwright;
try { playwright = require('playwright'); }
catch {
  const modules = suppliedModules || join(process.env.HOME, '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
  playwright = require(join(modules, 'playwright'));
}
const bundledBrowser = playwright.chromium.executablePath();
const candidates = [process.env.COLLECTION_CHROME_PATH, bundledBrowser,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean);
const executablePath = candidates.find(existsSync);
if (!executablePath) throw new Error('A Chromium executable is required. Set COLLECTION_CHROME_PATH to an installed executable.');
const output = join(root, 'docs/reports/data-collection');
const htmlPath = join(output, 'report.html');
const pdfPath = join(output, 'report.pdf');
const browser = await playwright.chromium.launch({ executablePath, headless: true });
let result;
try {
  const context = await browser.newContext({ viewport: { width: 1000, height: 1300 } });
  // Embedded images are local data URLs; no network is needed to render.
  await context.route(/^https?:\/\//, route => route.abort());
  const page = await context.newPage();
  await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load' });
  await page.emulateMedia({ media: 'print' });
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map(image => image.decode()));
  });
  const layout = await page.evaluate(() => [...document.querySelectorAll('article.page')].map((article, index) => {
    const content = article.querySelector('.content');
    const footer = article.querySelector('footer');
    const footerTop = footer.getBoundingClientRect().top;
    const lastBottom = Math.max(...[...content.children].map(child => child.getBoundingClientRect().bottom));
    return { page: index + 1, title: article.querySelector('h2').textContent,
      contentHeightPx: content.clientHeight, contentScrollHeightPx: content.scrollHeight,
      footerOverlapPx: Math.max(0, lastBottom - footerTop),
      pageHeightPx: article.getBoundingClientRect().height };
  }));
  await page.pdf({ path: pdfPath, preferCSSPageSize: true, printBackground: true,
    displayHeaderFooter: false, margin: { top: '0', right: '0', bottom: '0', left: '0' } });
  const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');
  result = { generated_at: new Date().toISOString(), browser: browser.version(),
    method: 'offline HTML -> Chromium print -> A4 PDF; no external requests',
    html_path: 'docs/reports/data-collection/report.html', pdf_path: 'docs/reports/data-collection/report.pdf',
    html_sha256: hash(htmlPath), pdf_sha256: hash(pdfPath),
    expected_pages: layout.length, layout,
    footer_overlap_pages: layout.filter(page => page.footerOverlapPx > 1).map(page => page.page),
    visual_pdf_review_status: 'pending' };
  writeFileSync(join(root, 'data/collection/evidence/pdf-export.json'), JSON.stringify(result, null, 2) + '\n');
} finally { await browser.close(); }
console.log(JSON.stringify({ expected_pages: result.expected_pages, footer_overlap_pages: result.footer_overlap_pages,
  pdf_sha256: result.pdf_sha256 }));
if (result.footer_overlap_pages.length) process.exitCode = 2;
