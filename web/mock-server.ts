// Serves fixtures/mock_api/ at /mock-api/ in `vite dev` and `vite preview`, and copies it into the build
// when VITE_DATA=mock, so the mock app works with no backend. Supports HTTP Range for video (iOS Safari
// and WebKit refuse to play an mp4 without it).
import { createReadStream, existsSync, statSync, cpSync } from 'node:fs';
import { extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Plugin } from 'vite';

export const MOCK_PREFIX = '/mock-api/';
export const FIXTURES_DIR = resolve(fileURLToPath(new URL('.', import.meta.url)), '../fixtures/mock_api');

const TYPES: Record<string, string> = {
  '.json': 'application/json; charset=utf-8',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.mp4': 'video/mp4',
  '.mov': 'video/quicktime',
};

function serveFixture(req: IncomingMessage, res: ServerResponse, next: () => void) {
  const url = req.url ?? '';
  if (!url.startsWith(MOCK_PREFIX)) return next();
  const rel = decodeURIComponent(url.slice(MOCK_PREFIX.length).split('?')[0]);
  const file = resolve(FIXTURES_DIR, rel);
  if (!file.startsWith(FIXTURES_DIR + sep) || !existsSync(file) || !statSync(file).isFile()) {
    res.statusCode = 404;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ detail: `No mock for ${rel}` }));
    return;
  }
  const size = statSync(file).size;
  res.setHeader('Content-Type', TYPES[extname(file).toLowerCase()] ?? 'application/octet-stream');
  res.setHeader('Accept-Ranges', 'bytes');
  res.setHeader('Cache-Control', 'no-cache');
  const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range ?? '');
  if (range) {
    const start = range[1] ? Number(range[1]) : Math.max(0, size - Number(range[2]));
    const end = range[1] && range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
    if (start >= size || start > end) {
      res.statusCode = 416;
      res.setHeader('Content-Range', `bytes */${size}`);
      res.end();
      return;
    }
    res.statusCode = 206;
    res.setHeader('Content-Range', `bytes ${start}-${end}/${size}`);
    res.setHeader('Content-Length', String(end - start + 1));
    createReadStream(file, { start, end }).pipe(res);
    return;
  }
  res.setHeader('Content-Length', String(size));
  createReadStream(file).pipe(res);
}

export function mockApiPlugin(dataMode: string): Plugin {
  let outDir = '';
  let isBuild = false;
  return {
    name: 'punchwalk-mock-api',
    configureServer(server) {
      server.middlewares.use(serveFixture);
    },
    configurePreviewServer(server) {
      server.middlewares.use(serveFixture);
    },
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
      // Vitest also runs plugins (with a placeholder outDir); only a real `vite build` copies fixtures.
      isBuild = config.command === 'build' && !process.env.VITEST;
    },
    closeBundle() {
      if (!isBuild || dataMode !== 'mock') return;
      if (!existsSync(FIXTURES_DIR)) {
        this.warn(`fixtures not found at ${FIXTURES_DIR}; the mock build has no data`);
        return;
      }
      cpSync(FIXTURES_DIR, join(outDir, 'mock-api'), {
        recursive: true,
        filter: (src) => !src.includes(`${sep}requests`) && !src.endsWith('README.md'),
      });
    },
  };
}
