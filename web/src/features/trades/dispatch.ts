// Sending a trade its list: which channels it can use and why, what the button says, and the request.
// The message itself is rendered by the API (DispatchPreviewOut); the UI shows it exactly as sent.
import {
  CHANNEL_LABEL,
  TRADE_LABEL,
  type Channel,
  type DispatchCreate,
  type DispatchOut,
  type DispatchPreviewOut,
  type Trade,
} from '../../api/types';
import { plural } from '../../lib/labels';

/** The request: channel and item ids are both required, in the preview's order. */
export function buildDispatch(channel: Channel, preview: Pick<DispatchPreviewOut, 'item_ids'>, excluded: Set<string> = new Set()): DispatchCreate {
  return { channel, item_ids: preview.item_ids.filter((id) => !excluded.has(id)) };
}

export interface ChannelOption {
  value: Channel;
  label: string;
  disabled: boolean;
  /** Why it can't be used ("No email on file for Clearview Glass."). */
  reason: string | null;
  destination: string | null;
}

const ORDER: Channel[] = ['slack', 'email', 'outbox'];

export function channelOptions(preview: Pick<DispatchPreviewOut, 'channels'>): ChannelOption[] {
  return ORDER.map((c) => {
    const ch = preview.channels.find((x) => x.channel === c);
    return {
      value: c,
      label: CHANNEL_LABEL[c],
      disabled: !ch?.available,
      reason: ch?.reason ?? null,
      destination: ch?.destination ?? null,
    };
  });
}

/** "Message to #punch-electrical" / "Email to maria.chen@example.com" / "Message to copy and share". */
export function destinationLine(channel: Channel, destination: string | null): string {
  if (channel === 'slack') return `Message to ${destination ?? 'Slack'}`;
  if (channel === 'email') return `Email to ${destination ?? 'the trade'}`;
  return 'Message to copy and share';
}

/** The sentence under the channel picker: the API's reason for the default, else where it goes. */
export function channelNote(preview: Pick<DispatchPreviewOut, 'channel' | 'channel_reason'>, chosen: ChannelOption): string | null {
  if (chosen.value === preview.channel && preview.channel_reason) return preview.channel_reason;
  if (chosen.value === 'outbox') return 'It’s saved in the Outbox, and the message is copied so you can paste it anywhere.';
  if (chosen.destination) return `Goes to ${chosen.destination}.`;
  return null;
}

/** Button words: "Send 3 items to Ace Electric", or for the Outbox "Copy 1 item for Clearview Glass". */
export function sendButtonLabel(channel: Channel, count: number, company: string): string {
  return channel === 'outbox' ? `Copy ${plural(count, 'item')} for ${company}` : `Send ${plural(count, 'item')} to ${company}`;
}

/** Toast words: "Sent 3 items to Ace Electric in Slack". */
export function sentToast(d: Pick<DispatchOut, 'channel' | 'item_ids' | 'company' | 'trade'>, copied = false): string {
  const who = d.company ?? TRADE_LABEL[d.trade];
  const n = plural(d.item_ids.length, 'item');
  if (d.channel === 'slack') return `Sent ${n} to ${who} in Slack`;
  if (d.channel === 'email') return `Sent ${n} to ${who} by email`;
  return copied ? `Copied ${n} for ${who}. It’s in the Outbox too.` : `Saved ${n} for ${who} in the Outbox`;
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

/** The text of a sent message, for the Outbox's copy button. */
export function dispatchText(d: Pick<DispatchOut, 'payload'>): string | null {
  const p = d.payload as Record<string, unknown>;
  for (const k of ['text', 'message_text', 'body']) if (typeof p[k] === 'string') return p[k] as string;
  return null;
}
