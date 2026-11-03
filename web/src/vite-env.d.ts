/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DATA?: 'mock' | 'live';
  /** '1' shows the one-line demo banner on the punch list. */
  readonly VITE_DEMO?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
