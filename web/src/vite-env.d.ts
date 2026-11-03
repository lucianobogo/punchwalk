/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DATA?: 'mock' | 'live';
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
