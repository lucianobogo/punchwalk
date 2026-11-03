// PLAN 5.1 AC: the manifest and icons are valid (valid JSON, icons exist at the declared sizes,
// apple-touch-icon linked), and index.html has the Apple meta tags DESIGN.md asks for.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const web = process.cwd();
const pub = resolve(web, 'public');

/** Width and height from a PNG's IHDR chunk. */
function pngSize(file: string): [number, number] {
  const b = readFileSync(file);
  expect(b.subarray(1, 4).toString('ascii')).toBe('PNG');
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
}

describe('PWA', () => {
  const manifest = JSON.parse(readFileSync(resolve(pub, 'manifest.webmanifest'), 'utf8'));

  it('declares the app as DESIGN.md says', () => {
    expect(manifest).toMatchObject({
      name: 'Punchwalk',
      short_name: 'Punchwalk',
      display: 'standalone',
      start_url: '/app',
      orientation: 'portrait',
      background_color: '#F2F4F5',
      theme_color: '#FFFFFF',
    });
  });

  it('has 192, 512 and maskable icons at their declared sizes', () => {
    const purposes = new Set<string>();
    for (const icon of manifest.icons as { src: string; sizes: string; purpose: string }[]) {
      const [w, h] = icon.sizes.split('x').map(Number);
      expect(pngSize(resolve(pub, icon.src.replace(/^\//, '')))).toEqual([w, h]);
      purposes.add(`${icon.purpose}:${w}`);
    }
    expect(purposes).toEqual(new Set(['any:192', 'any:512', 'maskable:512']));
  });

  it('links a 180 px apple-touch-icon and the Apple meta tags', () => {
    expect(pngSize(resolve(pub, 'apple-touch-icon.png'))).toEqual([180, 180]);
    const html = readFileSync(resolve(web, 'index.html'), 'utf8');
    expect(html).toContain('<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"');
    expect(html).toContain('<link rel="manifest" href="/manifest.webmanifest"');
    expect(html).toContain('<meta name="apple-mobile-web-app-capable" content="yes"');
    expect(html).toContain('<meta name="apple-mobile-web-app-status-bar-style" content="default"');
    expect(html).toMatch(/name="viewport" content="[^"]*viewport-fit=cover/);
  });
});
