// Item detail: which actions an item offers, and the plain words for its facts.
// DESIGN.md "Item detail → Bottom bar": one primary action by status, plus a More sheet.
import {
  PROVIDER_LABEL,
  SEVERITY_LABEL,
  STATUS_LABEL,
  TRADE_LABEL,
  type Grounding,
  type HistoryEntryOut,
  type ItemOut,
  type ItemStatus,
  type Provider,
  type TradeContactOut,
} from '../../api/types';

export type PrimaryKind = 'sign_off' | 'approve_send' | 'reopen';
export type MoreKind = 'send' | 'mark_needs_look' | 'edit' | 'share' | 'dismiss';

export interface ActionSpec<K extends string> {
  kind: K;
  label: string;
}

/** Who gets it: the company, or the trade in plain words when there is no contact. */
export function recipient(item: Pick<ItemOut, 'trade'>, contact: TradeContactOut | null | undefined): string {
  return contact?.company ?? TRADE_LABEL[item.trade];
}

/** Open: "Sign off". Needs a look: "Approve and send to <company>". Signed off: "Reopen". */
export function primaryAction(status: ItemStatus, company: string): ActionSpec<PrimaryKind> {
  switch (status) {
    case 'needs_review':
      return { kind: 'approve_send', label: `Approve and send to ${company}` };
    case 'closed':
      return { kind: 'reopen', label: 'Reopen' };
    default:
      return { kind: 'sign_off', label: 'Sign off' };
  }
}

/**
 * The More sheet, as DESIGN.md lists it plus the share link:
 * Open -> Send again, Mark as needs a look, Edit; Needs a look -> Dismiss item, Edit; Signed off -> (Reopen is primary).
 */
export function moreActions(item: Pick<ItemOut, 'status' | 'dispatch'>, company: string): ActionSpec<MoreKind>[] {
  const share: ActionSpec<MoreKind> = { kind: 'share', label: 'Copy link for the trade' };
  if (item.status === 'needs_review') {
    return [{ kind: 'dismiss', label: 'Dismiss item' }, { kind: 'edit', label: 'Edit' }, share];
  }
  if (item.status === 'closed') return [share];
  return [
    { kind: 'send', label: item.dispatch ? 'Send again' : `Send to ${company}` },
    { kind: 'mark_needs_look', label: 'Mark as needs a look' },
    { kind: 'edit', label: 'Edit' },
    share,
  ];
}

/** How the defect was located, in plain words (`spark_box`: the model's box, drawn as a rounded rectangle). */
export const GROUNDING_LABEL: Record<Grounding, string> = {
  sam_concept: 'Outline by SAM 3.1',
  sam_box: "Outline from the model's box",
  spark_box: 'Box from the model',
  pointed_only: 'Pointed location',
  none: 'Voice only',
};

/** Safe for values the UI doesn't know yet (a newer server): a neutral phrase instead of a blank. */
export function groundingLabel(g: string): string {
  return (GROUNDING_LABEL as Record<string, string>)[g] ?? 'Located by the models';
}

/** "Muse Spark Open on Baseten", or "Muse Spark 1.3 (fallback)" when Baseten failed twice. */
export function checkerName(provider: Provider): string {
  if (provider === 'meta-fallback') return 'Muse Spark 1.3 (fallback)';
  if (provider === 'baseten') return 'Muse Spark Open on Baseten';
  return `Muse Spark 1.3 on the ${PROVIDER_LABEL.meta}`;
}

const FIELD_LABEL: Record<string, string> = {
  title: 'title',
  description: 'summary',
  action: 'action',
  trade: 'trade',
  room: 'room',
  location_detail: 'location',
  severity: 'severity',
  status: 'status',
};

function valueLabel(field: string | null, v: string | null): string {
  if (v == null || v === '') return 'nothing';
  if (field === 'trade') return TRADE_LABEL[v as keyof typeof TRADE_LABEL] ?? v;
  if (field === 'severity') return SEVERITY_LABEL[v as keyof typeof SEVERITY_LABEL] ?? v;
  if (field === 'status') return STATUS_LABEL[v as ItemStatus] ?? v;
  return `“${v}”`;
}

/** One History line in plain words. Human edits say so: every edit is a human override. */
export function historyText(h: HistoryEntryOut): string {
  const you = h.actor === 'human';
  switch (h.action) {
    case 'edited': {
      const f = FIELD_LABEL[h.field ?? ''] ?? h.field ?? 'item';
      return `${you ? 'You changed' : 'Changed'} the ${f} from ${valueLabel(h.field, h.from)} to ${valueLabel(h.field, h.to)}.`;
    }
    case 'status_changed': {
      if (you && h.to === 'closed') return 'You signed it off.';
      if (you && h.to === 'open' && h.from === 'closed') return 'You reopened it.';
      if (you && h.to === 'open' && h.from === 'needs_review') return 'You approved it.';
      if (you && h.to === 'needs_review') return 'You marked it as needs a look.';
      return h.note ?? `Status changed to ${valueLabel('status', h.to)}.`;
    }
    case 'dismissed':
      return h.note ?? (you ? 'You dismissed it.' : 'Dismissed.');
    default:
      return h.note ?? fallbackText(h);
  }
}

function fallbackText(h: HistoryEntryOut): string {
  switch (h.action) {
    case 'created':
      return 'Written by Muse Spark Open on Baseten.';
    case 'verified':
      return 'Checked against the photo and the quote.';
    case 'flagged':
      return 'Set aside for a human look.';
    case 'dispatched':
      return 'Sent to the trade.';
    case 'rewalk_checked':
      return 'Checked on the re-walk.';
    default:
      return 'Noted.';
  }
}
