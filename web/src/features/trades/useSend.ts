// Send items to a trade, then say what happened ("Posted 2 items to Slack"). A Copy link send also puts
// the message on the clipboard, so it can be pasted into a text or a group chat right away.
import { useSendDispatch } from '../../api/hooks';
import type { Channel, DispatchOut, Trade } from '../../api/types';
import { useToast } from '../../components';
import { dispatchText, sentSummary } from './dispatch';

async function copy(text: string | null): Promise<boolean> {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function useSendToTrade() {
  const send = useSendDispatch();
  const toast = useToast();

  /** `channel` null = let the server use the trade's effective channel. */
  async function run(projectId: string, trade: Trade, channel: Channel | null, itemIds: string[], fallbackText?: string): Promise<DispatchOut | null> {
    try {
      const out = await send.mutateAsync({ projectId, trade, body: channel ? { channel, item_ids: itemIds } : { item_ids: itemIds } });
      if (!out.ok) {
        toast.show(out.error ?? 'It didn’t go through. Try again, or choose another channel.', { tone: 'error' });
        return out;
      }
      if (out.channel === 'outbox') await copy(dispatchText(out) ?? fallbackText ?? null);
      toast.show(sentSummary(out));
      return out;
    } catch (e) {
      toast.show(e instanceof Error && e.message ? e.message : 'It didn’t go through. Check the connection and try again.', { tone: 'error' });
      return null;
    }
  }

  return { run, isPending: send.isPending };
}

export { copy as copyText };
