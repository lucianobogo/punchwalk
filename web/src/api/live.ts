// Live adapter: HTTP to the FastAPI app (same origin in production; Vite proxies /api and /media in dev).
import { ApiError, type Api, type UploadProgress, type UploadRequest } from './api';
import { filtersToSearchParams } from './filters';
import type { UploadOut } from './types';

async function readError(res: Response): Promise<ApiError> {
  let message = `The server answered ${res.status}. Try again in a moment.`;
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') message = body.detail;
  } catch {
    // not JSON; keep the generic message
  }
  return new ApiError(res.status, message);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
  });
  if (!res.ok) throw await readError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T>(path: string) => request<T>(path);
const send = <T>(method: string, path: string, body?: unknown) =>
  request<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

const enc = encodeURIComponent;

/** Multipart upload with byte progress (fetch can't report upload progress; XHR can). */
function uploadXhr(projectId: string, req: UploadRequest, onProgress?: UploadProgress): Promise<UploadOut> {
  const form = new FormData();
  form.append('kind', req.kind);
  if (req.rewalkOf) form.append('rewalk_of', req.rewalkOf);
  for (const clip of req.clips) form.append('clips', clip, clip.name);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/projects/${enc(projectId)}/walks`);
    xhr.responseType = 'json';
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(1);
        resolve(xhr.response as UploadOut);
      } else {
        const detail = (xhr.response as { detail?: unknown } | null)?.detail;
        reject(
          new ApiError(
            xhr.status,
            typeof detail === 'string' ? detail : `The upload failed (${xhr.status}). Check the connection and try again.`,
          ),
        );
      }
    };
    xhr.onerror = () => reject(new ApiError(0, 'The upload stopped. Check the connection and try again.'));
    xhr.send(form);
  });
}

export const liveApi: Api = {
  health: () => get('/healthz'),
  projects: () => get('/api/projects'),
  project: (id) => get(`/api/projects/${enc(id)}`),
  items: (id, filters = {}) => {
    const qs = filtersToSearchParams(filters).toString();
    return get(`/api/projects/${enc(id)}/items${qs ? `?${qs}` : ''}`);
  },
  item: (id) => get(`/api/items/${enc(id)}`),
  patchItem: (id, patch) => send('PATCH', `/api/items/${enc(id)}`, patch),
  dismissItem: (id) => send('DELETE', `/api/items/${enc(id)}`),
  shareItem: (id) => send('POST', `/api/items/${enc(id)}/share`),
  share: (token) => get(`/api/share/${enc(token)}`),
  walks: (projectId) => get(projectId ? `/api/projects/${enc(projectId)}/walks` : '/api/walks'),
  walk: (id) => get(`/api/walks/${enc(id)}`),
  overlay: (id) => get(`/api/walks/${enc(id)}/overlay`),
  progress: (id) => get(`/api/walks/${enc(id)}/progress`),
  retryWalk: (id) => send('POST', `/api/walks/${enc(id)}/retry`),
  modelCalls: (id) => get(`/api/walks/${enc(id)}/model-calls`),
  processing: (id) => get(`/api/walks/${enc(id)}/processing`),
  rewalkSummary: (id) => get(`/api/walks/${enc(id)}/rewalk-summary`),
  upload: uploadXhr,
  trades: (id) => get(`/api/projects/${enc(id)}/trades`),
  dispatchPreview: (id, trade, channel) =>
    get(`/api/projects/${enc(id)}/trades/${trade}/preview${channel ? `?channel=${channel}` : ''}`),
  dispatches: (id, trade) => get(`/api/projects/${enc(id)}/trades/${trade}/dispatches`),
  dispatch: (id, trade, body) => send('POST', `/api/projects/${enc(id)}/trades/${trade}/dispatch`, body),
};
