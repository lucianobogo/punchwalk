/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DATA?: 'mock' | 'live';
  /** '1' shows the one-line demo banner on the punch list. */
  readonly VITE_DEMO?: string;
  /** Landing page links (optional; hidden when unset). */
  readonly VITE_REPO_URL?: string;
  readonly VITE_VIDEO_URL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
