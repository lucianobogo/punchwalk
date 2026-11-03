// Send items to a trade with the right toast. Outbox sends also copy the message, so it can be pasted
// into a text or a group chat right away.
import { useSendDispatch } from '../../api/hooks';
import type { Channel, DispatchOut, Trade } from '../../api/types';
import { useToast } from '../../components';
import { dispatchText, sentToast } from './dispatch';

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

  async function run(projectId: string, trade: Trade, channel: Channel, itemIds: string[], fallbackText?: string): Promise<DispatchOut | null> {
    try {
      const out = await send.mutateAsync({ projectId, trade, body: { channel, item_ids: itemIds } });
      if (!out.ok) {
        toast.show(out.error ?? 'It didn’t go through. Try again, or choose another channel.', { tone: 'error' });
        return out;
      }
      const copied = channel === 'outbox' ? await copy(dispatchText(out) ?? fallbackText ?? null) : false;
      toast.show(sentToast(out, copied));
      return out;
    } catch (e) {
      toast.show(e instanceof Error && e.message ? e.message : 'It didn’t go through. Check the connection and try again.', { tone: 'error' });
      return null;
    }
  }

  return { run, isPending: send.isPending };
}

export { copy as copyText };
