import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { mockApiPlugin } from './mock-server.ts';

const API = 'http://localhost:8000';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const dataMode = process.env.VITE_DATA ?? env.VITE_DATA ?? 'mock';
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
