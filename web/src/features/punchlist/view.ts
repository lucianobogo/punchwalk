// The punch list's view state, kept entirely in the URL (DESIGN.md: "Filters are kept in the URL, so
// a filtered view can be shared"). Defaults are omitted from the URL, so "/" is the default view.
import { SEGMENT_STATUSES, filtersFromSearchParams, filtersToSearchParams, type ItemFilters, type Segment } from '../../api/filters';
import type { ItemOut } from '../../api/types';

export type Group = 'room' | 'walk' | 'trade';
export type Sent = 'any' | 'yes' | 'no';

export interface ListView {
  segment: Segment;
  /** trade / severity / room / q / sort. Status comes from the segment. */
  filters: Omit<ItemFilters, 'status'>;
  group: Group;
  /** Sent to the trade yet? (client-side; the API has no such filter) */
  sent: Sent;
}

export const DEFAULT_VIEW: ListView = { segment: 'todo', filters: {}, group: 'room', sent: 'any' };

const SEGMENTS: Segment[] = ['todo', 'signed_off', 'all'];
const GROUPS: Group[] = ['room', 'walk', 'trade'];

export function viewFromParams(p: URLSearchParams): ListView {
  const { status: _ignored, ...filters } = filtersFromSearchParams(p);
  const seg = p.get('show') as Segment | null;
  const group = p.get('group') as Group | null;
  const sent = p.get('sent');
  return {
    segment: seg && SEGMENTS.includes(seg) ? seg : 'todo',
    filters,
    group: group && GROUPS.includes(group) ? group : 'room',
    sent: sent === 'yes' || sent === 'no' ? sent : 'any',
  };
}

export function viewToParams(v: ListView): URLSearchParams {
  const p = filtersToSearchParams(v.filters);
  if (v.segment !== 'todo') p.set('show', v.segment);
  if (v.group !== 'room') p.set('group', v.group);
  if (v.sent !== 'any') p.set('sent', v.sent);
  return p;
}

/** Filters to hand to applyFilters (segment -> statuses). */
export function viewFilters(v: ListView): ItemFilters {
  return { ...v.filters, status: SEGMENT_STATUSES[v.segment] };
}

export function matchesSent(item: ItemOut, sent: Sent): boolean {
  if (sent === 'any') return true;
  return sent === 'yes' ? item.dispatch != null : item.dispatch == null;
}

/** True when anything narrows the list beyond the segment. */
export function hasFilters(v: ListView): boolean {
  const f = v.filters;
  return Boolean(f.trade?.length || f.severity?.length || f.room?.length || f.q || v.sent !== 'any');
}

export function clearFilters(v: ListView): ListView {
  return { ...v, filters: { sort: v.filters.sort }, sent: 'any' };
}
