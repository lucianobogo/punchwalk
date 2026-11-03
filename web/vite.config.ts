import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { mockApiPlugin } from './mock-server.ts';

const API = 'http://localhost:8000';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  // Live by default (PLAN 5.8): `pnpm build` and `pnpm dev` talk to the API. Mocks are for tests
  // (vitest forces them) and screenshots (screenshots/run.ts sets VITE_DATA=mock); `VITE_DATA=mock pnpm dev`
  // works offline.
  const dataMode = process.env.VITEST ? 'mock' : (process.env.VITE_DATA ?? env.VITE_DATA ?? 'live');
  return {
    base: '/',
    plugins: [react(), tailwindcss(), mockApiPlugin(dataMode)],
    define: {
      'import.meta.env.VITE_DATA': JSON.stringify(dataMode),
    },
    build: {
      outDir: '../punchwalk/static',
      emptyOutDir: true,
      assetsDir: 'assets',
    },
    server: {
      port: 5173,
      proxy: {
        '/api': API,
        '/media': API,
        '/healthz': API,
      },
    },
    preview: { port: 4173 },
    test: {
      environment: 'jsdom',
      globals: false,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      css: false,
    },
  };
});
