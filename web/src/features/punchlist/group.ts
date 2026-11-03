// Turns a filtered item list into sections: "Needs a look" pinned first, then rooms in walk order
// (or trades, or one flat walk-order list). Pure, so it's tested without rendering.
import { TRADE_LABEL } from '../../api/types';
import type { ItemOut, Severity } from '../../api/types';
import type { ItemSort } from '../../api/filters';
import type { Group } from './view';

export interface Section {
  key: string;
  /** null = no header (the flat walk-order list). */
  title: string | null;
  tone: 'default' | 'hold';
  items: ItemOut[];
}

const SEVERITY_RANK: Record<Severity, number> = { safety: 0, functional: 1, cosmetic: 2 };

function sortItems(items: ItemOut[], sort: ItemSort | undefined): ItemOut[] {
  const out = [...items];
  if (sort === 'severity') {
    out.sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || a.number - b.number);
  } else {
    out.sort((a, b) => a.number - b.number);
  }
  return out;
}

/**
 * @param roomOrder the project's rooms in walk order; rooms not listed follow in order of first item.
 */
export function groupItems(
  items: ItemOut[],
  opts: { group: Group; sort?: ItemSort; roomOrder?: string[] },
): Section[] {
  const sections: Section[] = [];
  const needs = items.filter((i) => i.status === 'needs_review');
  const rest = items.filter((i) => i.status !== 'needs_review');

  if (needs.length) {
    sections.push({ key: 'needs_look', title: 'Needs a look', tone: 'hold', items: sortItems(needs, opts.sort) });
  }
  if (!rest.length) return sections;

  if (opts.group === 'walk') {
    sections.push({ key: 'walk', title: null, tone: 'default', items: sortItems(rest, opts.sort) });
    return sections;
  }

  const keyOf = (i: ItemOut) => (opts.group === 'trade' ? i.trade : (i.room ?? ''));
  const buckets = new Map<string, ItemOut[]>();
  for (const it of [...rest].sort((a, b) => a.number - b.number)) {
    const k = keyOf(it);
    buckets.set(k, [...(buckets.get(k) ?? []), it]);
  }
  let keys = [...buckets.keys()]; // first-appearance (walk) order
  if (opts.group === 'room' && opts.roomOrder?.length) {
    const rank = (k: string) => {
      const i = opts.roomOrder!.indexOf(k);
      return i === -1 ? Number.MAX_SAFE_INTEGER : i;
    };
    keys = keys.map((k, i) => [k, i] as const).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([k]) => k);
  }
  for (const k of keys) {
    const title =
      opts.group === 'trade' ? TRADE_LABEL[k as keyof typeof TRADE_LABEL] : k === '' ? 'No room' : k;
    sections.push({ key: `${opts.group}:${k}`, title, tone: 'default', items: sortItems(buckets.get(k)!, opts.sort) });
  }
  return sections;
}

/** "Left of the closet door" -> "Left of the closet door." */
function sentence(s: string): string {
  const t = s.trim();
  if (!t) return t;
  const cap = t[0].toUpperCase() + t.slice(1);
  return /[.!?]$/.test(cap) ? cap : `${cap}.`;
}

/**
 * The row's summary line (DESIGN.md: up to 2 lines; for Needs a look, why it needs a look).
 * Otherwise trade (or room, when grouped by trade) plus where: "Painting. Left of the closet door."
 */
export function rowSummary(item: ItemOut, group: Group): string {
  if (item.status === 'needs_review') return sentence(item.review_reason ?? item.description);
  const lead = group === 'trade' ? (item.room ?? TRADE_LABEL[item.trade]) : TRADE_LABEL[item.trade];
  const where = item.location_detail ? ` ${sentence(item.location_detail)}` : '';
  return `${sentence(lead)}${where}`;
}
