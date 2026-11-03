// `pnpm screenshots` / `make screenshots`: builds the app in mock mode, serves it with `vite preview`
// (no backend), and captures every screen in screens.ts with Playwright WebKit using the iPhone 15 Pro
// profile at 393 x 852, plus a 1440 x 900 desktop context. Writes .verify/screens/<nn>-<name>.png and
// an index.html contact sheet that puts each shot next to its reviewed render.
//
//   node screenshots/run.ts                 all screens
//   node screenshots/run.ts punch item      only screens whose name contains "punch" or "item"
//   SCREENS_STRICT=1 node screenshots/run.ts   fail when a setup step fails (once the screens exist)
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { devices, webkit, type Browser, type BrowserContextOptions, type Page } from '@playwright/test';
import { build, preview } from 'vite';
import { SCREENS, type Screen } from './screens.ts';

const webDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repoDir = resolve(webDir, '..');
const outDir = resolve(repoDir, '.verify/screens');
const buildDir = resolve(webDir, 'node_modules/.cache/screens-build');
const strict = process.env.SCREENS_STRICT === '1';
const filters = process.argv.slice(2);

const PHONE: BrowserContextOptions = {
  ...devices['iPhone 15 Pro'],
  // The profile's viewport is Safari's visible area (393 x 659); DESIGN.md targets the full 393 x 852.
  viewport: { width: 393, height: 852 },
  // 2x matches the reviewed renders in docs/design/ and keeps files small.
  deviceScaleFactor: 2,
  colorScheme: 'light',
};
const DESKTOP: BrowserContextOptions = {
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  colorScheme: 'light',
};

interface Result {
  file: string;
  screen: Screen;
  notes: string[];
  ok: boolean;
}

/** Waits (at most 3 s) for images to finish. Lazy images outside a viewport-only shot never load, so skip them. */
async function settleImages(page: Page, includeLazy: boolean) {
  await page.evaluate(
    (lazy) =>
      Promise.race([
        Promise.all(
          [...document.images]
            .filter((i) => !i.complete && (lazy || i.loading !== 'lazy'))
            .map(
              (i) =>
                new Promise((r) => {
                  i.addEventListener('load', r, { once: true });
                  i.addEventListener('error', r, { once: true });
                }),
            ),
        ),
        new Promise((r) => setTimeout(r, 3000)),
      ]),
    includeLazy,
  );
}

async function capture(browser: Browser, base: string, screen: Screen, file: string): Promise<Result> {
  const notes: string[] = [];
  const context = await browser.newContext(screen.viewport === 'desktop' ? DESKTOP : PHONE);
  const page = await context.newPage();
  page.on('console', (m) => {
    if (m.type() === 'error') notes.push(`console: ${m.text()}`);
  });
  page.on('pageerror', (e) => notes.push(`page error: ${e.message}`));
  let ok = true;
  try {
    await page.goto(base + screen.path, { waitUntil: 'networkidle', timeout: 15000 });
    await page.evaluate(() => document.fonts.ready);
    if (screen.setup) {
      try {
        await screen.setup(page);
      } catch (e) {
        const msg = `setup failed: ${(e as Error).message.split('\n')[0]}`;
        notes.push(msg);
        if (strict) ok = false;
      }
    }
    if (screen.fullPage) {
      // Walk the page so loading="lazy" images below the fold start loading before a full-page capture.
      await page.evaluate(async () => {
        for (let y = 0; y < document.documentElement.scrollHeight; y += window.innerHeight) {
          window.scrollTo(0, y);
          await new Promise((r) => setTimeout(r, 60));
        }
        window.scrollTo(0, 0);
      });
    }
    await page.waitForLoadState('networkidle');
    await settleImages(page, Boolean(screen.fullPage));
    await page.waitForTimeout(250);
    await page.screenshot({ path: resolve(outDir, file), fullPage: screen.fullPage ?? false });
  } catch (e) {
    ok = false;
    notes.push(`failed: ${(e as Error).message.split('\n')[0]}`);
  } finally {
    await context.close();
  }
  return { file, screen, notes, ok };
}

function contactSheet(results: Result[]): string {
  const renders = relative(outDir, resolve(repoDir, 'docs/design'));
  const cards = results
    .map((r) => {
      const notes = r.notes.length
        ? `<ul>${r.notes.map((n) => `<li>${n.replace(/</g, '&lt;')}</li>`).join('')}</ul>`
        : '';
      const render = r.screen.render
        ? `<figure><img src="${renders}/${r.screen.render}" loading="lazy"><figcaption>Render ${r.screen.render}</figcaption></figure>`
        : '';
      return `<section class="${r.ok ? '' : 'bad'}">
  <h2>${r.file}</h2>
  <p>${r.screen.checklist}<br><code>${r.screen.path}</code></p>
  <div class="pair"><figure><a href="${r.file}"><img src="${r.file}" loading="lazy"></a><figcaption>Screenshot</figcaption></figure>${render}</div>
  ${notes}
</section>`;
    })
    .join('\n');
  return `<!doctype html><html><head><meta charset="utf-8"><title>Punchwalk screenshots</title>
<style>
body{font:14px/1.4 system-ui,sans-serif;background:#F2F4F5;color:#1B2430;margin:24px}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:20px}
section{background:#fff;border:1px solid #DCE1E5;border-radius:14px;padding:12px}
section.bad{border-color:#D93B30}
h2{font-size:15px;margin:0 0 4px} p{margin:0 0 8px;color:#5B6673}
.pair{display:flex;gap:8px;align-items:flex-start} figure{margin:0;flex:1}
img{width:100%;border:1px solid #DCE1E5;border-radius:8px} figcaption{font-size:12px;color:#5B6673}
ul{color:#8A5A0B;font-size:12px;padding-left:18px}
</style></head><body><h1>Punchwalk screenshots</h1>
<p>${new Date().toISOString()}. WebKit, iPhone 15 Pro profile at 393 x 852 (2x), desktop at 1440 x 900. Mock data.</p>
<main>${cards}</main></body></html>`;
}

async function main() {
  process.env.VITE_DATA = 'mock';
  console.log('building (mock data)...');
  await build({
    root: webDir,
    configFile: resolve(webDir, 'vite.config.ts'),
    logLevel: 'warn',
    build: { outDir: buildDir, emptyOutDir: true },
  });
  const server = await preview({
    root: webDir,
    configFile: resolve(webDir, 'vite.config.ts'),
    logLevel: 'warn',
    build: { outDir: buildDir },
    preview: { port: 4179, strictPort: false, host: '127.0.0.1' },
  });
  const base = (server.resolvedUrls?.local[0] ?? 'http://127.0.0.1:4179/').replace(/\/$/, '');

  mkdirSync(outDir, { recursive: true });
  if (!filters.length) {
    for (const f of readdirSync(outDir)) {
      if (f.endsWith('.png') || f === 'index.html') rmSync(resolve(outDir, f));
    }
  }

  const browser = await webkit.launch();
  const results: Result[] = [];
  try {
    for (const [i, screen] of SCREENS.entries()) {
      if (filters.length && !filters.some((f) => screen.name.includes(f))) continue;
      const file = `${String(i + 1).padStart(2, '0')}-${screen.name}.png`;
      const r = await capture(browser, base, screen, file);
      results.push(r);
      const flag = r.ok ? (r.notes.length ? 'note' : 'ok  ') : 'FAIL';
      console.log(`${flag} ${file}${r.notes.length ? `  (${r.notes.join('; ')})` : ''}`);
    }
  } finally {
    await browser.close();
    await new Promise<void>((done) => server.httpServer.close(() => done()));
  }

  if (!filters.length) writeFileSync(resolve(outDir, 'index.html'), contactSheet(results));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} screens written to ${relative(repoDir, outDir)}/`);
  if (failed.length) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
