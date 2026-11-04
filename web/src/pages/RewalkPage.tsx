// Re-walk summary (DESIGN.md "Re-walk summary", 07-rewalk-summary.png): the headline and a segmented bar,
// the flagged item first on a Hold-tinted card with before/after, then Signed off, then still-open items
// with Send again, then anything the re-walk didn't check.
import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useApproveAndSend, useItems, useRewalkSummary, useSendDispatch } from '../api/hooks';
import { NOT_CHECKED_ON_REWALK, type ItemOut, type RewalkItemOut, type RewalkSummaryOut } from '../api/types';
import { Button, ButtonLink, Card, PushedBar, SectionHeader, Skeleton, TapeTab, useToast } from '../components';
import { barLabel, barSegments, openByTrade, rewalkSections } from '../features/rewalk/summary';
import { useSendToTrade } from '../features/trades/useSend';
import { formatDateTime, formatShortDate } from '../lib/format';
import { plural } from '../lib/labels';
import { cx } from '../lib/cx';
import { ErrorState } from './common';

const SEGMENT_COLOR = {
  signed_off: 'bg-signed',
  still_open: 'bg-tape',
  needs_a_look: 'bg-hold',
  not_checked: 'bg-hairline',
} as const;

export default function RewalkPage() {
  const { id } = useParams();
  const summary = useRewalkSummary(id);
  const s = summary.data;
  return (
    <>
      <PushedBar
        backTo="/walks"
        backLabel="Walks"
        title="Re-walk"
        subtitle={s?.recorded_at ? `${formatDateTime(s.recorded_at)}. ${plural(s.counts.checked, 'item')} checked.` : undefined}
      />
      <div className="px-4 pt-4">
        {summary.isLoading ? (
          <div role="status" aria-label="Loading the re-walk summary">
            <Skeleton className="h-7 w-4/5 rounded-full" />
            <Skeleton className="mt-6 h-96 w-full rounded-card" />
          </div>
        ) : summary.isError || !s ? (
          <ErrorState error={summary.error} onRetry={() => summary.refetch()} notFound="There’s no re-walk summary for this walk." />
        ) : (
          <Body s={s} />
        )}
      </div>
    </>
  );
}

function Body({ s }: { s: RewalkSummaryOut }) {
  const items = useItems(s.project_id);
  const byId = new Map((items.data?.items ?? []).map((i) => [i.id, i]));
  const sender = useSendToTrade();
  const sendMany = useSendDispatch();
  const approve = useApproveAndSend();
  // A needs-a-look item can't simply be sent again: approve it and send it (the server picks the channel).
  const approveOne = async (it: RewalkItemOut) => {
    setSendingId(it.item_id);
    try {
      const out = await approve.mutateAsync({ itemId: it.item_id });
      toast.show(`${out.summary}. Item ${it.number} approved.`);
    } catch (e) {
      toast.show(e instanceof Error && e.message ? e.message : 'That didn’t go through. Try again in a moment.', { tone: 'error' });
    } finally {
      setSendingId(null);
    }
  };
  const toast = useToast();
  const [busyAll, setBusyAll] = useState(false);

  // Track which item is sending, so only its button says "Sending…".
  const [sendingId, setSendingId] = useState<string | null>(null);
  const sendOne = async (it: RewalkItemOut) => {
    setSendingId(it.item_id);
    try {
      await sender.run(s.project_id, it.trade, null, [it.item_id]);
    } finally {
      setSendingId(null);
    }
  };
  /** Sent after this re-walk already? Then say so instead of offering Send again. */
  const resentAt = (it: RewalkItemOut): string | null => {
    const d = byId.get(it.item_id)?.dispatch?.sent_at;
    // Compare as times, not strings ("Z" vs "+00:00" must not matter).
    return d && s.recorded_at && Date.parse(d) > Date.parse(s.recorded_at) ? d : null;
  };

  async function sendAllOpen() {
    const groups = openByTrade(s.still_open.filter((it) => !resentAt(it)));
    if (!groups.size) return;
    setBusyAll(true);
    let sent = 0;
    try {
      for (const [trade, ids] of groups) {
        await sendMany.mutateAsync({ projectId: s.project_id, trade, body: { item_ids: ids } });
        sent += ids.length;
      }
      toast.show(`Sent ${plural(sent, 'item')} again to ${plural(groups.size, 'trade')}`);
    } catch (e) {
      toast.show(e instanceof Error && e.message ? e.message : 'Not everything went out. Try again.', { tone: 'error' });
    } finally {
      setBusyAll(false);
    }
  }

  const unsentOpen = s.still_open.filter((it) => !resentAt(it)).length;

  return (
    <>
      <h2 className="text-card font-semibold text-graphite">{s.headline}</h2>
      <div role="img" aria-label={barLabel(s.counts)} className="mt-3 flex h-2.5 gap-1">
        {barSegments(s.counts).map((seg) => (
          <span key={seg.key} className={cx('rounded-full', SEGMENT_COLOR[seg.key])} style={{ flexGrow: seg.fraction, flexBasis: 0 }} />
        ))}
      </div>

      {rewalkSections(s).map((sec) => {
        if (sec.kind === 'needs_a_look') {
          return sec.items.map((it) => (
            <FlaggedCard key={it.item_id} it={it} onApprove={() => approveOne(it)} busy={sendingId === it.item_id} />
          ));
        }
        return (
          <section key={sec.kind} aria-label={sec.title} className="mt-6">
            <div className="mb-2.5 flex items-center">
              <SectionHeader className="flex-1" count={sec.items.length}>
                {sec.title}
              </SectionHeader>
              {sec.kind === 'still_open' && unsentOpen > 1 && (
                <Button variant="quiet" onClick={sendAllOpen} busy={busyAll} busyLabel="Sending…" className="-mr-2">
                  Send all {unsentOpen} again
                </Button>
              )}
            </div>
            <Card padded={false} className="divide-y divide-hairline overflow-hidden">
              {sec.items.map((it) => (
                <Row
                  key={it.item_id}
                  it={it}
                  kind={sec.kind}
                  item={byId.get(it.item_id)}
                  resentAt={resentAt(it)}
                  onSend={() => sendOne(it)}
                  busy={sendingId === it.item_id}
                />
              ))}
            </Card>
          </section>
        );
      })}

      <p className="mt-6 text-center">
        <Link to={`/walks/${s.walk_id}`} className="inline-flex min-h-11 items-center text-body text-tape-ink underline">
          Watch the re-walk
        </Link>
      </p>
    </>
  );
}

function FlaggedCard({ it, onApprove, busy }: { it: RewalkItemOut; onApprove: () => void; busy: boolean }) {
  return (
    <Card tone="flagged" className="mt-5">
      <SectionHeader tone="hold" as="div">
        Needs a look
      </SectionHeader>
      <div className="mt-2.5 flex items-center gap-3">
        <TapeTab number={it.number} decorative />
        <h3 className="min-w-0 flex-1 text-card font-semibold text-graphite">
          <span className="sr-only">{`Item ${it.number}: `}</span>
          {it.title}
        </h3>
      </div>
      {it.reason && <p className="mt-2.5 text-body text-graphite">{it.reason}</p>}
      {it.quote && <p className="mt-1 text-secondary text-pencil">On the re-walk: “{it.quote}”</p>}
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Photo src={it.before_image_url} caption={it.before_at ? formatShortDate(it.before_at) : 'Before'} alt={`${it.title}, before`} />
        <Photo
          src={it.after_image_url}
          caption={it.after_at ? `${formatShortDate(it.after_at)}, re-walk` : 'Re-walk'}
          alt={`${it.title}, on the re-walk`}
        />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Button onClick={onApprove} busy={busy} busyLabel="Sending…">
          Approve and send
        </Button>
        <ButtonLink to={`/items/${it.item_id}`} variant="secondary">
          Open item
        </ButtonLink>
      </div>
    </Card>
  );
}

function Photo({ src, caption, alt }: { src: string | null; caption: string; alt: string }) {
  return (
    <figure>
      {src ? (
        <img src={src} alt={alt} className="aspect-[4/3] w-full rounded-image bg-track object-cover" />
      ) : (
        <span className="flex aspect-[4/3] w-full items-center justify-center rounded-image bg-track text-secondary text-pencil">No photo</span>
      )}
      <figcaption className="mt-1 text-secondary text-pencil">{caption}</figcaption>
    </figure>
  );
}

function Row({
  it,
  kind,
  item,
  resentAt,
  onSend,
  busy,
}: {
  it: RewalkItemOut;
  kind: 'signed_off' | 'still_open' | 'not_checked' | 'needs_a_look';
  item: ItemOut | undefined;
  resentAt: string | null;
  onSend: () => void;
  busy: boolean;
}) {
  return (
    <div className="flex min-h-14 items-center gap-3 py-2 pr-2 pl-4">
      <Link to={`/items/${it.item_id}`} className="-my-2 flex min-h-14 min-w-0 flex-1 items-center gap-3 self-stretch py-2">
        <TapeTab number={it.number} badge={kind === 'signed_off' ? 'signed' : null} decorative />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-card text-graphite">
            <span className="sr-only">{`Item ${it.number}: `}</span>
            {it.title}
          </span>
          {kind === 'still_open' && it.quote && <span className="block truncate text-secondary text-pencil">“{it.quote}”</span>}
          {/* The section header already says "Not checked on re-walk"; only a different reason is worth a line. */}
          {kind === 'not_checked' && it.reason && it.reason !== NOT_CHECKED_ON_REWALK && (
            <span className="block text-secondary text-pencil">{it.reason}</span>
          )}
        </span>
      </Link>
      {kind === 'signed_off' && <span className="shrink-0 pr-2 text-body text-signed-ink">Signed off</span>}
      {kind === 'still_open' &&
        (resentAt ? (
          <span className="shrink-0 pr-2 text-secondary text-pencil">Sent again {formatShortDate(resentAt)}</span>
        ) : (
          <Button variant="quiet" onClick={onSend} busy={busy} busyLabel="Sending…" aria-label={`Send item ${it.number} again${item?.dispatch?.company ? ` to ${item.dispatch.company}` : ''}`}>
            Send again
          </Button>
        ))}
    </div>
  );
}
