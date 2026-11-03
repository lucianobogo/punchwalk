// Every item action with its toast. Status changes offer Undo; nothing here apologizes.
import { useNavigate } from 'react-router';
import { useDismissItem, usePatchItem, useSendDispatch, useShareItem } from '../../api/hooks';
import type { ItemOut, ItemPatch, TradeContactOut } from '../../api/types';
import { useToast } from '../../components';
import { pickChannel, recipient, type MoreKind, type PrimaryKind } from './actions';

const errorText = (e: unknown) =>
  e instanceof Error && e.message ? e.message : 'That didn’t go through. Check the connection and try again.';

export function useItemActions(item: ItemOut | undefined, contact: TradeContactOut | null | undefined) {
  const toast = useToast();
  const navigate = useNavigate();
  const patch = usePatchItem();
  const send = useSendDispatch();
  const dismiss = useDismissItem();
  const share = useShareItem();
  const busy = patch.isPending || send.isPending || dismiss.isPending;

  async function setStatus(p: ItemPatch, message: string) {
    if (!item) return;
    const before = item.status;
    try {
      await patch.mutateAsync({ id: item.id, patch: p });
      toast.show(message, {
        action: {
          label: 'Undo',
          onClick: () => patch.mutate({ id: item.id, patch: { status: before, note: 'Undone.' } }),
        },
      });
    } catch (e) {
      toast.show(errorText(e), { tone: 'error' });
    }
  }

  async function sendToTrade(approveFirst: boolean) {
    if (!item) return;
    const who = recipient(item, contact);
    try {
      if (approveFirst) await patch.mutateAsync({ id: item.id, patch: { status: 'open', review_reason: null } });
      await send.mutateAsync({
        projectId: item.project_id,
        trade: item.trade,
        body: { channel: pickChannel(contact), item_ids: [item.id] },
      });
      toast.show(`Sent item ${item.number} to ${who}`);
    } catch (e) {
      toast.show(errorText(e), { tone: 'error' });
    }
  }

  async function copyShareLink() {
    if (!item) return;
    try {
      const link = await share.mutateAsync(item.id);
      const url = new URL(link.url, window.location.origin).toString();
      try {
        await navigator.clipboard.writeText(url);
        toast.show('Link copied. Paste it in a message to the trade.');
      } catch {
        toast.show(`Copy this link: ${url}`, { tone: 'info', durationMs: 8000 });
      }
    } catch (e) {
      toast.show(errorText(e), { tone: 'error' });
    }
  }

  async function dismissItem() {
    if (!item) return;
    try {
      await dismiss.mutateAsync(item.id);
      toast.show(`Item ${item.number} dismissed`);
      navigate('/', { replace: true });
    } catch (e) {
      toast.show(errorText(e), { tone: 'error' });
    }
  }

  function runPrimary(kind: PrimaryKind) {
    if (kind === 'sign_off') return setStatus({ status: 'closed' }, `Signed off item ${item?.number}`);
    if (kind === 'reopen') return setStatus({ status: 'open' }, `Reopened item ${item?.number}`);
    return sendToTrade(true);
  }

  /** `edit` is handled by the page (it opens the edit sheet). */
  function runMore(kind: Exclude<MoreKind, 'edit'>) {
    switch (kind) {
      case 'send':
        return sendToTrade(false);
      case 'mark_needs_look':
        return setStatus(
          { status: 'needs_review', review_reason: 'You marked it for a second look.' },
          `Item ${item?.number} moved to Needs a look`,
        );
      case 'share':
        return copyShareLink();
      case 'dismiss':
        return dismissItem();
    }
  }

  async function saveEdits(p: ItemPatch) {
    if (!item) return false;
    try {
      await patch.mutateAsync({ id: item.id, patch: p });
      toast.show('Saved. The change is in History.');
      return true;
    } catch (e) {
      toast.show(errorText(e), { tone: 'error' });
      return false;
    }
  }

  return { busy, runPrimary, runMore, saveEdits };
}
