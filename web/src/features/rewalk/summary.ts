// Re-walk summary (DESIGN.md, 07-rewalk-summary.png): flagged items first, then Signed off, then still
// open, then not checked. Pure grouping, so it's tested without rendering.
import { NOT_CHECKED_ON_REWALK, type RewalkItemOut, type RewalkSummaryOut, type Trade } from '../../api/types';

export type RewalkSectionKind = 'needs_a_look' | 'signed_off' | 'still_open' | 'not_checked';

export interface RewalkSection {
  kind: RewalkSectionKind;
  title: string;
  items: RewalkItemOut[];
}

const TITLES: Record<RewalkSectionKind, string> = {
  needs_a_look: 'Needs a look',
  signed_off: 'Signed off',
  still_open: 'Still open',
  not_checked: NOT_CHECKED_ON_REWALK,
};

export function rewalkSections(s: RewalkSummaryOut): RewalkSection[] {
  const order: RewalkSectionKind[] = ['needs_a_look', 'signed_off', 'still_open', 'not_checked'];
  return order
    .map((kind) => ({ kind, title: TITLES[kind], items: [...s[kind]].sort((a, b) => a.number - b.number) }))
    .filter((sec) => sec.items.length > 0);
}

/** The segmented bar: signed off, still open, needs a look (and not checked), as fractions. */
export function barSegments(c: RewalkSummaryOut['counts']) {
  const parts = [
    { key: 'signed_off', value: c.signed_off },
    { key: 'still_open', value: c.still_open },
    { key: 'needs_a_look', value: c.needs_a_look },
    { key: 'not_checked', value: c.not_checked },
  ] as const;
  const total = parts.reduce((n, p) => n + p.value, 0) || 1;
  return parts.filter((p) => p.value > 0).map((p) => ({ ...p, fraction: p.value / total }));
}

/** Accessible label for the bar, in words. */
export function barLabel(c: RewalkSummaryOut['counts']): string {
  const bits = [`${c.signed_off} signed off`, `${c.still_open} still open`, `${c.needs_a_look} ${c.needs_a_look === 1 ? 'needs' : 'need'} a look`];
  if (c.not_checked) bits.push(`${c.not_checked} not checked`);
  return `Of ${c.checked + c.not_checked} items: ${bits.join(', ')}.`;
}

/** Still-open items grouped by trade, for "Send the open items again" (one message per trade). */
export function openByTrade(items: Pick<RewalkItemOut, 'item_id' | 'trade'>[]): Map<Trade, string[]> {
  const m = new Map<Trade, string[]>();
  for (const it of items) m.set(it.trade, [...(m.get(it.trade) ?? []), it.item_id]);
  return m;
}
