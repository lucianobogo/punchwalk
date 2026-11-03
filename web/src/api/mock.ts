// Mock adapter: answers every Api call from fixtures/mock_api/ (naming convention in its README.md),
// so the app, the tests and `make screenshots` run with no backend.
//
//   GET  /api/<path>          -> <path>.json           (e.g. walks/wlk_2b_first/overlay.json)
//   POST /api/<path>          -> <path>.post.json      (e.g. projects/prj_unit3a/walks.post.json)
//   GET  /healthz             -> healthz.json
//   /media/<file>             -> media/<file>          (URLs are rewritten to /mock-api/media/...)
//
// Query strings are ignored by the fixtures: the item list is stored unfiltered, and this adapter
// filters and recounts it. Writes (patch, dismiss, dispatch, upload) change an in-memory copy, so
// toasts and status changes can be demoed; reload the page to reset.
//
// Scenario switches (URL query, kept for the browser session; also on window.__punchwalkMock):
//   ?mock_scenario=first_run   no walks and no items (first-run empty state)
//   ?mock_scenario=todo_empty  every item signed off ("Everything is signed off.")
//   ?mock_scenario=offline     every call fails as if the server were down
//   ?mock_progress=5           pin the processing replay (walks/wlk_3a_first/progress/05.json)
//   ?mock_latency=300          delay every answer (ms), to see skeletons
//   ?mock_reset=1              clear the switches above
import { ApiError, OFFLINE_MESSAGE, type Api } from './api';
import { applyFilters, countItems } from './filters';
import {
  TRADE_LABEL,
  type DispatchListOut,
  type DispatchOut,
  type DispatchPreviewOut,
  type HealthOut,
  type HistoryEntryOut,
  type ItemListOut,
  type ItemOut,
  type ItemPatch,
  type JobProgressOut,
  type ModelCallListOut,
  type OverlayOut,
  type ProcessingSummaryOut,
  type ProjectListOut,
  type ProjectOut,
  type RewalkSummaryOut,
  type ShareItemOut,
  type ShareLinkOut,
  type Trade,
  type TradeListOut,
  type UploadOut,
  type WalkDetailOut,
  type WalkListOut,
  type WalkOut,
} from './types';

export const MOCK_BASE = '/mock-api/';

/** Returns the raw text of a fixture file (path relative to fixtures/mock_api/). */
export type MockLoader = (file: string) => Promise<string>;

export type MockScenario = 'first_run' | 'todo_empty' | 'offline' | null;

export interface MockSettings {
  scenario: MockScenario;
  /** Pin the processing replay to snapshot N (1..8); null = follow the clock after an upload. */
  progressStep: number | null;
  latencyMs: number;
  /** Seconds per replay snapshot after a mock upload. */
  replayStepS: number;
}

const REPLAY_STEPS = 8;
/** Walks with a progress/NN.json replay (README: "replay of a run"). */
const REPLAY_WALKS = new Set(['wlk_3a_first']);
const UPLOAD_PROJECT = 'prj_unit3a';

const fetchLoader: MockLoader = async (file) => {
  const res = await fetch(MOCK_BASE + file);
  if (!res.ok) throw new ApiError(404, `No mock for ${file}`);
  return res.text();
};

const clone = <T>(v: T): T => structuredClone(v);
const nowIso = () => new Date().toISOString();
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function createMockApi(loader: MockLoader = fetchLoader, initial: Partial<MockSettings> = {}) {
  const settings: MockSettings = {
    scenario: null,
    progressStep: null,
    latencyMs: 0,
    replayStepS: 2.5,
    ...initial,
  };

  // ------------------------------------------------------------------ fixture loading
  const cache = new Map<string, Promise<unknown>>();

  async function load<T>(file: string): Promise<T> {
    let p = cache.get(file);
    if (!p) {
      p = loader(file).then(
        (text) => JSON.parse(text.replaceAll('"/media/', `"${MOCK_BASE}media/`)),
        (e) => {
          throw e instanceof ApiError ? e : new ApiError(404, `No mock for ${file}`);
        },
      );
      p.catch(() => cache.delete(file));
      cache.set(file, p);
    }
    return clone((await p) as T);
  }

  async function answer<T>(fn: () => Promise<T>): Promise<T> {
    if (settings.latencyMs > 0) await sleep(settings.latencyMs);
    if (settings.scenario === 'offline') throw new ApiError(0, OFFLINE_MESSAGE);
    return fn();
  }

  // ------------------------------------------------------------------ in-memory writes
  const patched = new Map<string, ItemOut>();
  const dismissed = new Set<string>();
  const sent = new Map<string, DispatchOut[]>(); // key: project/trade, newest first
  const uploadStartedAt = new Map<string, number>();

  const overlay = (it: ItemOut): ItemOut => {
    let out = patched.get(it.id) ?? it;
    if (settings.scenario === 'todo_empty' && out.status !== 'closed') out = { ...out, status: 'closed' };
    return out;
  };

  function replayStep(walkId: string): number | null {
    if (!REPLAY_WALKS.has(walkId)) return null;
    if (settings.progressStep != null) return Math.min(REPLAY_STEPS, Math.max(1, settings.progressStep));
    const t0 = uploadStartedAt.get(walkId);
    if (t0 == null) return null;
    return Math.min(REPLAY_STEPS, 1 + Math.floor((Date.now() - t0) / (settings.replayStepS * 1000)));
  }

  async function progressFor(walkId: string): Promise<JobProgressOut> {
    const step = replayStep(walkId);
    if (step == null) return load(`walks/${walkId}/progress.json`);
    return load(`walks/${walkId}/progress/${String(step).padStart(2, '0')}.json`);
  }

  /** All live items of a project: fixture list + items a running replay has written, with writes applied. */
  async function projectItems(projectId: string): Promise<ItemOut[]> {
    if (settings.scenario === 'first_run') return [];
    const list = await load<ItemListOut>(`projects/${projectId}/items.json`);
    const items = list.items;
    const have = new Set(items.map((i) => i.id));
    for (const walkId of REPLAY_WALKS) {
      if (replayStep(walkId) == null) continue;
      const prog = await progressFor(walkId);
      for (const thumb of prog.items_so_far) {
        if (have.has(thumb.id)) continue;
        const it = await load<ItemOut>(`items/${thumb.id}.json`);
        if (it.project_id === projectId) {
          items.push(it);
          have.add(it.id);
        }
      }
    }
    return items.filter((i) => !dismissed.has(i.id)).map(overlay);
  }

  async function withLiveCounts(p: ProjectOut): Promise<ProjectOut> {
    const items = await projectItems(p.id);
    const out = { ...p, counts: countItems(items) };
    if (settings.scenario === 'first_run') return { ...out, walk_count: 0, last_walk_at: null };
    return out;
  }

  async function withReplay(w: WalkOut): Promise<WalkOut> {
    if (replayStep(w.id) == null) return w;
    const prog = await progressFor(w.id);
    return { ...w, status: prog.walk_status, stages: prog.stages };
  }

  function historyEntry(partial: Partial<HistoryEntryOut>): HistoryEntryOut {
    return { at: nowIso(), actor: 'human', action: 'edited', field: null, from: null, to: null, note: null, ...partial };
  }

  async function getItem(itemId: string): Promise<ItemOut> {
    if (dismissed.has(itemId)) throw new ApiError(404, 'This item was dismissed.');
    return overlay(await load<ItemOut>(`items/${itemId}.json`));
  }

  async function companyFor(projectId: string, trade: Trade): Promise<string | null> {
    const p = await load<ProjectOut>(`projects/${projectId}.json`);
    return p.trades.find((t) => t.trade === trade)?.company ?? null;
  }

  // ------------------------------------------------------------------ the Api
  const api: Api = {
    health: () => answer(() => load<HealthOut>('healthz.json')),

    projects: () =>
      answer(async () => {
        const list = await load<ProjectListOut>('projects.json');
        return { projects: await Promise.all(list.projects.map(withLiveCounts)) };
      }),

    project: (id) => answer(async () => withLiveCounts(await load<ProjectOut>(`projects/${id}.json`))),

    items: (id, filters = {}) => answer(async () => applyFilters(await projectItems(id), id, filters)),

    item: (id) => answer(() => getItem(id)),

    patchItem: (id, patch: ItemPatch) =>
      answer(async () => {
        const item = await getItem(id);
        const next: ItemOut = { ...item, history: [...item.history], updated_at: nowIso() };
        const rec = next as unknown as Record<string, unknown>;
        for (const [key, value] of Object.entries(patch)) {
          if (key === 'note' || value === undefined) continue;
          // null means "unchanged", except review_reason, where null clears it (approve).
          if (value === null && key !== 'review_reason') continue;
          const from = rec[key];
          if (from === value) continue;
          rec[key] = value;
          if (key === 'review_reason') continue; // follows the status change; not its own history line
          next.history.push(
            historyEntry({
              action: key === 'status' ? 'status_changed' : 'edited',
              field: key,
              from: from == null ? null : String(from),
              to: value == null ? null : String(value),
              note: patch.note ?? null,
            }),
          );
        }
        if (patch.note && !Object.keys(patch).some((k) => k !== 'note')) {
          next.history.push(historyEntry({ action: 'noted', note: patch.note }));
        }
        if (patch.status && patch.status !== 'needs_review' && patch.review_reason === undefined) {
          next.review_reason = null;
        }
        patched.set(id, next);
        return clone(next);
      }),

    dismissItem: (id) =>
      answer(async () => {
        await getItem(id);
        dismissed.add(id);
      }),

    shareItem: (id) => answer(() => load<ShareLinkOut>(`items/${id}/share.post.json`)),

    share: (token) =>
      answer(async () => {
        const s = await load<ShareItemOut>(`share/${token}.json`);
        return s;
      }),

    walks: (projectId) =>
      answer(async () => {
        if (settings.scenario === 'first_run') return { walks: [] };
        const list = await load<WalkListOut>(projectId ? `projects/${projectId}/walks.json` : 'walks.json');
        return { walks: await Promise.all(list.walks.map(withReplay)) };
      }),

    walk: (id) =>
      answer(async () => {
        const d = await load<WalkDetailOut>(`walks/${id}.json`);
        d.walk = await withReplay(d.walk);
        d.items = d.items.filter((i) => !dismissed.has(i.id)).map(overlay);
        return d;
      }),

    overlay: (id) => answer(() => load<OverlayOut>(`walks/${id}/overlay.json`)),

    progress: (id) => answer(() => progressFor(id)),

    retryWalk: (id) =>
      answer(async () => {
        uploadStartedAt.set(id, Date.now());
        return progressFor(id);
      }),

    modelCalls: (id) => answer(() => load<ModelCallListOut>(`walks/${id}/model-calls.json`)),

    processing: (id) => answer(() => load<ProcessingSummaryOut>(`walks/${id}/processing.json`)),

    rewalkSummary: (id) => answer(() => load<RewalkSummaryOut>(`walks/${id}/rewalk-summary.json`)),

    upload: async (_projectId, req, onProgress) => {
      // The fixtures have one upload: it starts the wlk_3a_first replay (README "The scenario").
      const total = req.clips.reduce((n, f) => n + f.size, 0);
      const ticks = 8;
      for (let i = 1; i <= ticks; i++) {
        await sleep(total > 0 ? 120 : 40);
        onProgress?.(i / ticks);
      }
      const out = await load<UploadOut>(`projects/${UPLOAD_PROJECT}/walks.post.json`);
      uploadStartedAt.set(out.walk_id, Date.now());
      settings.progressStep = null;
      return out;
    },

    trades: (id) =>
      answer(async () => {
        const list = await load<TradeListOut>(`projects/${id}/trades.json`);
        const items = await projectItems(id);
        for (const t of list.trades) {
          const mine = items.filter((i) => i.trade === t.trade);
          t.open_count = mine.filter((i) => i.status === 'open' || i.status === 'dispatched').length;
          t.needs_review_count = mine.filter((i) => i.status === 'needs_review').length;
          t.signed_off_count = mine.filter((i) => i.status === 'closed').length;
          const latest = sent.get(`${id}/${t.trade}`)?.[0];
          if (latest) t.last_sent_at = latest.sent_at;
        }
        return list;
      }),

    dispatchPreview: (id, trade, channel) =>
      answer(async () => {
        const p = await load<DispatchPreviewOut>(`projects/${id}/trades/${trade}/preview.json`);
        const items = await projectItems(id);
        const sendable = new Set(
          items.filter((i) => i.trade === trade && (i.status === 'open' || i.status === 'dispatched')).map((i) => i.id),
        );
        const before = p.lines.length;
        p.lines = p.lines.filter((l) => sendable.has(l.item_id));
        p.item_ids = p.item_ids.filter((x) => sendable.has(x));
        if (p.lines.length !== before) {
          const project = await load<ProjectOut>(`projects/${id}.json`);
          const n = p.lines.length;
          p.heading = `${project.name}: ${n} ${TRADE_LABEL[trade].toLowerCase()} ${n === 1 ? 'item' : 'items'}`;
        }
        if (channel && p.channels.some((c) => c.channel === channel)) p.channel = channel;
        return p;
      }),

    dispatches: (id, trade) =>
      answer(async () => {
        const list = await load<DispatchListOut>(`projects/${id}/trades/${trade}/dispatches.json`);
        list.dispatches = [...(sent.get(`${id}/${trade}`) ?? []), ...list.dispatches];
        return list;
      }),

    dispatch: (id, trade, body) =>
      answer(async () => {
        // Built from the trade's preview (no probing for optional dispatch.post.json files, which
        // would log 404s in the console).
        const company = await companyFor(id, trade);
        const preview = await load<DispatchPreviewOut>(`projects/${id}/trades/${trade}/preview.json`);
        const base: DispatchOut = {
          id: '',
          project_id: id,
          trade,
          company,
          channel: body.channel,
          destination: preview.channels.find((c) => c.channel === body.channel)?.destination ?? null,
          item_ids: [],
          sent_at: '',
          ok: true,
          error: null,
          payload: { text: body.channel === 'slack' ? preview.slack_text : preview.message_text },
        };
        const key = `${id}/${trade}`;
        const out: DispatchOut = {
          ...base,
          id: `dsp_mock_${trade}_${(sent.get(key)?.length ?? 0) + 1}`,
          channel: body.channel,
          item_ids: body.item_ids,
          sent_at: nowIso(),
        };
        sent.set(key, [out, ...(sent.get(key) ?? [])]);
        for (const itemId of body.item_ids) {
          const item = await getItem(itemId);
          patched.set(itemId, {
            ...item,
            status: item.status === 'open' ? 'dispatched' : item.status,
            dispatch: {
              dispatch_id: out.id,
              trade,
              company: out.company,
              channel: out.channel,
              sent_at: out.sent_at,
              times_sent: (item.dispatch?.times_sent ?? 0) + 1,
            },
            history: [
              ...item.history,
              historyEntry({ action: 'dispatched', note: `Sent to ${out.company ?? TRADE_LABEL[trade]}.` }),
            ],
          });
        }
        return clone(out);
      }),

    approveAndSend: (itemId, channel) =>
      answer(async () => {
        const item = await api.patchItem(itemId, { status: 'open', review_reason: null });
        const project = await load<ProjectOut>(`projects/${item.project_id}.json`);
        const contact = project.trades.find((t) => t.trade === item.trade);
        const ch = channel ?? (contact?.has_slack_webhook ? 'slack' : contact?.email ? 'email' : 'outbox');
        return api.dispatch(item.project_id, item.trade, { channel: ch, item_ids: [itemId] });
      }),
  };

  const control = {
    settings,
    setScenario(s: MockScenario) {
      settings.scenario = s;
    },
    setProgressStep(n: number | null) {
      settings.progressStep = n;
    },
    setLatency(ms: number) {
      settings.latencyMs = ms;
    },
    /** Drop every in-memory write. */
    reset() {
      patched.clear();
      dismissed.clear();
      sent.clear();
      uploadStartedAt.clear();
    },
  };

  return { api, control };
}

export type MockControl = ReturnType<typeof createMockApi>['control'];

const SETTINGS_KEY = 'punchwalk.mock';

/** Reads ?mock_* switches from the URL (kept in sessionStorage so in-app navigation keeps them). */
export function settingsFromLocation(): Partial<MockSettings> {
  if (typeof window === 'undefined') return {};
  let saved: Partial<MockSettings> = {};
  try {
    saved = JSON.parse(sessionStorage.getItem(SETTINGS_KEY) ?? '{}');
  } catch {
    saved = {};
  }
  const q = new URLSearchParams(window.location.search);
  if (q.has('mock_reset')) saved = {};
  const scenario = q.get('mock_scenario');
  if (scenario === 'first_run' || scenario === 'todo_empty' || scenario === 'offline') saved.scenario = scenario;
  if (scenario === 'none') saved.scenario = null;
  const step = q.get('mock_progress');
  if (step) saved.progressStep = Number(step) || null;
  const latency = q.get('mock_latency');
  if (latency) saved.latencyMs = Number(latency) || 0;
  try {
    sessionStorage.setItem(SETTINGS_KEY, JSON.stringify(saved));
  } catch {
    // private mode: switches apply to this page load only
  }
  return saved;
}
