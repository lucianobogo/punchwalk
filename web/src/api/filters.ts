// Item-list filters: one shape for the URL, the live API query string and the mock adapter.
import type {
  FacetsOut,
  ItemCountsOut,
  ItemListOut,
  ItemOut,
  ItemStatus,
  Severity,
  Trade,
} from './types';

export type ItemSort = 'walk' | 'severity';

export interface ItemFilters {
  trade?: Trade[];
  severity?: Severity[];
  room?: string[];
  status?: ItemStatus[];
  q?: string;
  sort?: ItemSort;
}

/** Punch-list segments (DESIGN.md: To do = Open + Needs a look, the default). */
export type Segment = 'todo' | 'signed_off' | 'all';

export const SEGMENT_STATUSES: Record<Segment, ItemStatus[] | undefined> = {
  todo: ['open', 'dispatched', 'needs_review'],
  signed_off: ['closed'],
  all: undefined,
};

const LIST_KEYS = ['trade', 'severity', 'room', 'status'] as const;

/** Repeated keys mean OR (`?trade=electrical&trade=plumbing`), as the API expects. */
export function filtersToSearchParams(f: ItemFilters): URLSearchParams {
  const p = new URLSearchParams();
  for (const key of LIST_KEYS) for (const v of f[key] ?? []) p.append(key, v);
  if (f.q) p.set('q', f.q);
  if (f.sort && f.sort !== 'walk') p.set('sort', f.sort);
  return p;
}

export function filtersFromSearchParams(p: URLSearchParams): ItemFilters {
  const f: ItemFilters = {};
  const trade = p.getAll('trade') as Trade[];
  const severity = p.getAll('severity') as Severity[];
  const room = p.getAll('room');
  const status = p.getAll('status') as ItemStatus[];
  if (trade.length) f.trade = trade;
  if (severity.length) f.severity = severity;
  if (room.length) f.room = room;
  if (status.length) f.status = status;
  const q = p.get('q');
  if (q) f.q = q;
  const sort = p.get('sort');
  if (sort === 'severity' || sort === 'walk') f.sort = sort;
  return f;
}

/** Stable key for react-query. */
export function filtersKey(f: ItemFilters): string {
  return filtersToSearchParams(f).toString();
}

const SEVERITY_RANK: Record<Severity, number> = { safety: 0, functional: 1, cosmetic: 2 };

export function matchesFilters(item: ItemOut, f: ItemFilters): boolean {
  if (f.trade?.length && !f.trade.includes(item.trade)) return false;
  if (f.severity?.length && !f.severity.includes(item.severity)) return false;
  if (f.room?.length && !(item.room && f.room.includes(item.room))) return false;
  if (f.status?.length && !f.status.includes(item.status)) return false;
  if (f.q) {
    const q = f.q.toLowerCase();
    const hay = [item.title, item.description, item.action, item.room, item.location_detail, item.quote]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    if (!hay.includes(q) && String(item.number) !== q) return false;
  }
  return true;
}

export function countItems(items: ItemOut[]): ItemCountsOut {
  const c = { todo: 0, open: 0, needs_review: 0, signed_off: 0, all: items.length };
  for (const it of items) {
    if (it.status === 'closed') c.signed_off++;
    else {
      c.todo++;
      if (it.status === 'needs_review') c.needs_review++;
      else c.open++;
    }
  }
  return c;
}

export function facetItems(items: ItemOut[]): FacetsOut {
  const f: FacetsOut = { trade: {}, severity: {}, room: {}, status: {} };
  for (const it of items) {
    f.trade[it.trade] = (f.trade[it.trade] ?? 0) + 1;
    f.severity[it.severity] = (f.severity[it.severity] ?? 0) + 1;
    if (it.room) f.room[it.room] = (f.room[it.room] ?? 0) + 1;
    f.status[it.status] = (f.status[it.status] ?? 0) + 1;
  }
  return f;
}

/**
 * Filter and sort an unfiltered list client-side (what the server does in live mode).
 * Facets and counts describe the unfiltered list, per the contract.
 */
export function applyFilters(all: ItemOut[], projectId: string, f: ItemFilters): ItemListOut {
  const items = all.filter((it) => matchesFilters(it, f));
  if (f.sort === 'severity') {
    items.sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || a.number - b.number);
  } else {
    items.sort((a, b) => a.number - b.number);
  }
  return {
    project_id: projectId,
    items,
    facets: facetItems(all),
    counts: countItems(all),
    total: items.length,
  };
}
