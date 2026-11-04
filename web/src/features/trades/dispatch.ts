// Sending a trade its list (DESIGN.md "Trades"): one vocabulary (To review, To send, Sent), the effective
// channel named by what it does, and buttons and history that say what happens or happened.
// The message itself is rendered by the API (DispatchPreviewOut); the UI shows it exactly as sent.
import {
  CHANNEL_LABEL,
  TRADE_LABEL,
  type Channel,
  type DispatchChannelOut,
  type DispatchCreate,
  type DispatchOut,
  type DispatchPreviewOut,
  type Trade,
} from '../../api/types';
import { plural } from '../../lib/labels';

/** The request: channel and item ids are both required. To-send items first (preview order), then re-sends. */
export function buildDispatch(
  channel: Channel,
  toSend: string[],
  excluded: Set<string>,
  resend: Set<string>,
): DispatchCreate & { channel: Channel } {
  return { channel, item_ids: [...toSend.filter((id) => !excluded.has(id)), ...[...resend].filter((id) => !toSend.includes(id))] };
}

/** "1 to review, 2 to send, 3 sent." or "All caught up." (zeros left out; sentences, not dot-joined meta). */
export function countsSentence(c: { to_review: number; to_send: number; sent: number }): string {
  const parts = [c.to_review && `${c.to_review} to review`, c.to_send && `${c.to_send} to send`, c.sent && `${c.sent} sent`].filter(Boolean);
  if (!c.to_review && !c.to_send) return c.sent ? `All caught up. ${c.sent} sent.` : 'All caught up.';
  return `${parts.join(', ')}.`;
}

/** Where a channel goes, as the API words it ("Posts to #punch-electrical."); a fallback if it didn't. */
export function channelLine(ch: Pick<DispatchChannelOut, 'channel' | 'destination'> & { label?: string | null }): string {
  if (ch.label) return ch.label;
  if (ch.channel === 'slack') return ch.destination ? `Posts to ${ch.destination}.` : "Posts to your team's Slack channel.";
  if (ch.channel === 'email') return ch.destination ? `Emails ${ch.destination}.` : 'Emails the trade.';
  return 'Copy a link to share.';
}

/** The picker: working channels only, the effective one first. Shown only when there's a choice. */
export function channelChoices(preview: Pick<DispatchPreviewOut, 'channels' | 'effective_channel'>): { value: Channel; label: string; line: string }[] {
  const working = preview.channels.filter((c) => c.available !== false);
  working.sort((a, b) => Number(b.channel === preview.effective_channel) - Number(a.channel === preview.effective_channel));
  return working.map((c) => ({ value: c.channel, label: CHANNEL_LABEL[c.channel], line: channelLine(c) }));
}

/** The button names the outcome: "Post 2 items to Slack" / "Email 2 items to Brightline Painting" / "Copy a link for 2 items". */
export function sendButtonLabel(channel: Channel, count: number, company: string): string {
  const n = plural(count, 'item');
  if (channel === 'slack') return `Post ${n} to Slack`;
  if (channel === 'email') return `Email ${n} to ${company}`;
  return `Copy a link for ${n}`;
}

/** What happened ("Posted 2 items to Slack"): the API's summary, or the same words built here. */
export function sentSummary(
  d: Pick<DispatchOut, 'channel' | 'item_ids' | 'company' | 'trade'> & { summary?: string | null; destination?: string | null },
): string {
  if (d.summary) return d.summary;
  const n = plural(d.item_ids.length, 'item');
  if (d.channel === 'slack') return `Posted ${n} to ${d.destination?.startsWith('#') ? d.destination : 'Slack'}`;
  if (d.channel === 'email') return `Emailed ${n} to ${d.company ?? TRADE_LABEL[d.trade]}`;
  return `Copied a link for ${n}`;
}

/** "Sent Nov 3 at 7:41 PM, Slack" for an item in "Already sent". */
export function sentWhere(channel: Channel, when: string): string {
  return `Sent ${when}, ${CHANNEL_LABEL[channel]}`;
}

/** The amber row: "1 item needs your review before it can go out". */
export function reviewLine(n: number): string {
  return `${plural(n, 'item')} ${n === 1 ? 'needs' : 'need'} your review before ${n === 1 ? 'it' : 'they'} can go out`;
}

/**
 * The heading with the count the user actually picked ("Unit 2B final punch: 1 electrical item").
 * Falls back to the API's heading when it doesn't have the usual shape.
 */
export function headingFor(heading: string, count: number, trade: Trade, total: number): string {
  if (count === total) return heading;
  const m = /^(.*): \d+ (.+?) items?$/.exec(heading);
  if (!m) return heading;
  return `${m[1]}: ${count} ${m[2] || TRADE_LABEL[trade].toLowerCase()} ${count === 1 ? 'item' : 'items'}`;
}

/** The text of a sent message, for the history's copy button. */
export function dispatchText(d: Pick<DispatchOut, 'payload'>): string | null {
  const p = d.payload as Record<string, unknown>;
  for (const k of ['text', 'message_text', 'body']) if (typeof p[k] === 'string') return p[k] as string;
  return null;
}
