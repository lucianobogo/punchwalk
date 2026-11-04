// Plain-word labels (DESIGN.md "Words"). The maps themselves live in the API contract so the
// Python side and the UI can't drift; this file only re-exports them and adds the few helpers
// that turn an item into a sentence.
import {
  CHANNEL_LABEL,
  MODEL_LABEL,
  PROVIDER_LABEL,
  SEVERITY_LABEL,
  STAGE_LABEL,
  STATUS_LABEL,
  TRADE_LABEL,
  WALK_KIND_LABEL,
  WALK_STATUS_LABEL,
} from '../api/types';
import type { ItemOut, ItemStatus, Severity, Trade } from '../api/types';
import { formatShortDate } from './format';

export {
  CHANNEL_LABEL,
  MODEL_LABEL,
  PROVIDER_LABEL,
  SEVERITY_LABEL,
  STAGE_LABEL,
  STATUS_LABEL,
  TRADE_LABEL,
  WALK_KIND_LABEL,
  WALK_STATUS_LABEL,
};

/** The three statuses the UI ever shows. */
export type UiStatus = 'open' | 'needs_look' | 'signed_off';

export function uiStatus(status: ItemStatus): UiStatus {
  if (status === 'needs_review') return 'needs_look';
  if (status === 'closed') return 'signed_off';
  return 'open';
}

export const UI_STATUS_LABEL: Record<UiStatus, string> = {
  open: 'Open',
  needs_look: 'Needs a look',
  signed_off: 'Signed off',
};

export const statusLabel = (s: ItemStatus) => STATUS_LABEL[s];
export const severityLabel = (s: Severity) => SEVERITY_LABEL[s];
export const tradeLabel = (t: Trade) => TRADE_LABEL[t];
export const modelLabel = (model: string) => MODEL_LABEL[model] ?? model;

/** "Open, sent to Oakline Millwork Nov 3". Sending is a fact about an open item, not a status. */
export function statusDetail(item: Pick<ItemOut, 'status' | 'dispatch'>): string {
  const label = STATUS_LABEL[item.status];
  if (uiStatus(item.status) !== 'open' || !item.dispatch) return label;
  const who = item.dispatch.company ?? TRADE_LABEL[item.dispatch.trade];
  return `${label}, sent to ${who} ${formatShortDate(item.dispatch.sent_at)}`;
}

/** "Send 3 items to Ace Electric" / "Send 1 item to Ace Electric". */
export function sendLabel(count: number, company: string): string {
  return `Send ${plural(count, 'item')} to ${company}`;
}

/** plural(3, 'item') -> "3 items" */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "wall-mounted cabinet, right door" -> "Wall-mounted cabinet, right door" (model text isn't always capitalized). */
export function capitalize(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
