// Picks the data source. VITE_DATA=mock (default until PLAN 5.8) answers from fixtures/mock_api;
// VITE_DATA=live talks to the FastAPI app.
import type { Api } from './api';
import { liveApi } from './live';
import { createMockApi, settingsFromLocation, type MockControl } from './mock';

export type DataMode = 'mock' | 'live';

export const DATA_MODE: DataMode = import.meta.env.VITE_DATA === 'live' ? 'live' : 'mock';

let mockControl: MockControl | null = null;

function makeApi(): Api {
  if (DATA_MODE === 'live') return liveApi;
  const { api, control } = createMockApi(undefined, { latencyMs: 0, ...settingsFromLocation() });
  mockControl = control;
  if (typeof window !== 'undefined') {
    (window as unknown as { __punchwalkMock?: MockControl }).__punchwalkMock = control;
  }
  return api;
}

let current: Api = makeApi();

/** The active adapter. Screens call it through the hooks in hooks.ts. */
export const api: Api = new Proxy({} as Api, {
  get: (_t, key: keyof Api) => current[key],
});

/** Tests swap the adapter (e.g. a mock reading fixtures from disk). */
export function setApi(next: Api) {
  current = next;
}

export function getMockControl(): MockControl | null {
  return mockControl;
}
