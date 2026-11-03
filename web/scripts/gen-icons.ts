// Generates the PWA icons from one SVG motif: the tape-tab strip with a graphite check on Primer
// (DESIGN.md "PWA"). Renders with Playwright WebKit, so no image libraries are needed.
//   node scripts/gen-icons.ts   (or: pnpm icons)
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { webkit } from '@playwright/test';

const here = dirname(fileURLToPath(import.meta.url));
const pub = resolve(here, '../public');

// Same values as styles/index.css (Primer, Tape tab, Graphite).
const PRIMER = '#F2F4F5';
const TAPE_TAB = '#5AA2EA';
const GRAPHITE = '#1B2430';
const TAPE_PATH = 'M3 0H37L39 3L37 6L40 9L37 12L39 15L37 18L40 21L37 24H3L1 21L3 18L0 15L3 12L1 9L3 6L0 3Z';

/** `fill` = share of the icon width the strip spans (maskable icons keep it inside the safe zone). */
function iconSvg(fill: number, opts: { rounded?: boolean } = {}): string {
  const size = 512;
  const w = size * fill;
  const s = w / 40; // path is 40 x 24
  const h = 24 * s;
  const x = (size - w) / 2;
  const y = (size - h) / 2;
  const bg = opts.rounded
    ? `<rect width="${size}" height="${size}" rx="112" fill="${PRIMER}"/>`
    : `<rect width="${size}" height="${size}" fill="${PRIMER}"/>`;
  // Check mark centered on the strip, in strip units.
  const check = 'M14.5 12.4 L18.6 16.3 L26 8.2';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
  ${bg}
  <g transform="rotate(-3 ${size / 2} ${size / 2}) translate(${x} ${y}) scale(${s})">
    <path d="${TAPE_PATH}" fill="${TAPE_TAB}"/>
    <path d="${check}" fill="none" stroke="${GRAPHITE}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
  </g>
</svg>
`;
}

const targets: { file: string; px: number; svg: string }[] = [
  { file: 'icons/icon-192.png', px: 192, svg: iconSvg(0.74) },
  { file: 'icons/icon-512.png', px: 512, svg: iconSvg(0.74) },
  // Maskable: the safe zone is a circle of 80% diameter, so keep the strip narrower.
  { file: 'icons/icon-maskable-512.png', px: 512, svg: iconSvg(0.6) },
  { file: 'apple-touch-icon.png', px: 180, svg: iconSvg(0.7) },
];

async function main() {
  mkdirSync(resolve(pub, 'icons'), { recursive: true });
  writeFileSync(resolve(pub, 'icons/icon.svg'), iconSvg(0.78, { rounded: true }));
  const browser = await webkit.launch();
  try {
    for (const t of targets) {
      const page = await browser.newPage({ viewport: { width: t.px, height: t.px }, deviceScaleFactor: 1 });
      const html = `<!doctype html><html><body style="margin:0">${t.svg.replace(
        /width="512" height="512"/,
        `width="${t.px}" height="${t.px}"`,
      )}</body></html>`;
      await page.setContent(html);
      const out = resolve(pub, t.file);
      await page.screenshot({ path: out, clip: { x: 0, y: 0, width: t.px, height: t.px } });
      await page.close();
      console.log(`wrote ${t.file} (${t.px}x${t.px})`);
    }
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
