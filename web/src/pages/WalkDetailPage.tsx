// Walk detail, the demo's hero screen (DESIGN.md "Walks", 04-walk-detail.png). Processing walks show the
// live checklist instead of the video; failed walks say what happened and offer Try again.
import { useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { useOverlay, useProgress, useWalk } from '../api/hooks';
import type { ItemOut, TurnOut, WalkKind } from '../api/types';
import { Card, EmptyState, MiniRow, PushedBar, SegmentedControl, Skeleton, TapeTab } from '../components';
import { ProcessingBody } from './RecordPage';
import { HERO_HEIGHT, WalkPlayer } from '../features/walk/WalkPlayer';
import { activeTurnIndex, momentState, seekTargetFor } from '../features/walk/moments';
import { formatClock, formatShortDate } from '../lib/format';
import { WALK_KIND_LABEL, plural } from '../lib/labels';
import { cx } from '../lib/cx';
import { ErrorState } from './common';

export default function WalkDetailPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const walk = useWalk(id);
  const w = walk.data?.walk;
  const when = w?.recorded_at ?? w?.created_at;
  const ready = w?.status === 'ready';

  return (
    <>
      <PushedBar
        backTo="/walks"
        backLabel="Walks"
        sticky={!ready}
        title={w ? WALK_KIND_LABEL[w.kind] : undefined}
        meta={w && when ? `${formatShortDate(when)}, ${plural(w.counts.items, 'item')}` : undefined}
      />
      <div className="h-3" />
      {walk.isLoading ? (
        <div role="status" aria-label="Loading the walk" className="animate-pulse-soft bg-track" style={{ height: HERO_HEIGHT }} />
      ) : walk.isError || !walk.data || !w ? (
        <div className="px-4">
          <ErrorState error={walk.error} onRetry={() => walk.refetch()} notFound="This walk isn’t here anymore." />
        </div>
      ) : ready ? (
        <ReadyWalk detail={walk.data} startAt={params.has('t') ? Number(params.get('t')) || 0 : null} />
      ) : (
        <div className="px-4">
          <NotReady walkId={w.id} projectId={w.project_id} kind={w.kind} />
        </div>
      )}
    </>
  );
}

function NotReady({ walkId, projectId, kind }: { walkId: string; projectId: string; kind: WalkKind }) {
  const progress = useProgress(walkId);
  if (progress.isLoading) return <Skeleton className="h-80 w-full rounded-card" />;
  if (!progress.data) return <ErrorState error={progress.error} onRetry={() => progress.refetch()} />;
  return <ProcessingBody progress={progress.data} walkProjectId={projectId} walkKind={kind} />;
}

type Tab = 'items' | 'transcript';

function ReadyWalk({ detail, startAt }: { detail: NonNullable<ReturnType<typeof useWalk>['data']>; startAt: number | null }) {
  const w = detail.walk;
  const overlay = useOverlay(w.id);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(startAt ?? 0);
  const [tab, setTab] = useState<Tab>('items');
  const itemsById = useMemo(() => new Map(detail.items.map((i) => [i.id, i])), [detail.items]);
  const events = useMemo(() => {
    const ev = overlay.data?.events ?? [];
    // Items with no pointing (voice-only) still get a moment, from the quote.
    const have = new Set(ev.map((e) => e.item_id));
    const extra = detail.items
      .filter((i) => !have.has(i.id) && i.quote_start_s != null)
      .map((i) => ({ t: i.quote_start_s!, item_id: i.id, number: i.number }));
    return [...ev, ...extra].sort((a, b) => a.t - b.t);
  }, [overlay.data, detail.items]);
  const moments = momentState(events, time);
  const seek = (t: number) => {
    const v = videoRef.current;
    if (v) v.currentTime = Math.max(0, t);
  };
  const overlayWithAll = overlay.data ? { ...overlay.data, events } : undefined;

  return (
    <>
      <WalkPlayer
        walk={w}
        overlay={overlayWithAll}
        videoRef={videoRef}
        moments={moments}
        itemsById={itemsById}
        startAt={startAt}
        onTime={setTime}
      />
      <div className="px-4 pt-4">
        <SegmentedControl<Tab>
          label="Show"
          value={tab}
          onChange={setTab}
          options={[
            { value: 'items', label: 'Items so far', count: moments.soFar.length },
            { value: 'transcript', label: 'Transcript' },
          ]}
        />
        <div className="mt-3">
          {tab === 'items' ? (
            moments.listed.length ? (
              <Card padded={false} className="divide-y divide-hairline overflow-hidden">
                {moments.listed.map((e) => {
                  const it = itemsById.get(e.item_id);
                  return (
                    <MiniRow
                      key={e.item_id}
                      number={e.number}
                      badge={it?.status === 'needs_review' ? 'hold' : it?.status === 'closed' ? 'signed' : null}
                      title={it?.title ?? `Item ${e.number}`}
                      trailing={formatClock(e.t)}
                      onClick={() => seek(seekTargetFor(e))}
                    />
                  );
                })}
              </Card>
            ) : (
              <EmptyState
                className="py-8"
                title={moments.card ? 'The first item is on the video.' : 'Items appear here as the video reaches them.'}
                body="Play the walk, or tap a marker on the timeline."
              />
            )
          ) : (
            <Transcript turns={detail.turns} time={time} itemsById={itemsById} onSeek={seek} />
          )}
          {tab === 'items' && moments.later > 0 && moments.listed.length > 0 && (
            <p className="mt-3 text-secondary text-pencil">{plural(moments.later, 'more item')} later in the walk.</p>
          )}
        </div>
        <div className="mt-5 flex flex-col items-center">
          <Link to={`/walks/${w.id}/processing`} className="inline-flex min-h-11 items-center text-body text-tape-ink underline">
            How this walk was processed
          </Link>
          {w.kind === 'rewalk' && (
            <Link to={`/walks/${w.id}/rewalk`} className="inline-flex min-h-11 items-center text-body text-tape-ink underline">
              What the re-walk signed off
            </Link>
          )}
        </div>
      </div>
    </>
  );
}

const CLAIM_WORDS = { fixed: 'Said fixed', not_fixed: 'Said not fixed', unclear: 'Unclear' } as const;

function Transcript({
  turns,
  time,
  itemsById,
  onSeek,
}: {
  turns: TurnOut[];
  time: number;
  itemsById: Map<string, ItemOut>;
  onSeek: (t: number) => void;
}) {
  const active = activeTurnIndex(turns, time);
  if (!turns.length) return <EmptyState className="py-8" title="Nothing was said on this walk." />;
  return (
    <Card padded={false} className="overflow-hidden">
      <ol aria-label="Transcript">
        {turns.map((t, i) => {
          const numbers = t.item_ids.map((id) => itemsById.get(id)?.number).filter((n): n is number => n != null);
          return (
            <li key={t.id}>
              <button
                type="button"
                onClick={() => onSeek(t.start_ms / 1000)}
                aria-current={i === active ? 'true' : undefined}
                className={cx(
                  'flex w-full items-start gap-3 border-b border-hairline px-4 py-3 text-left last:border-b-0',
                  i === active ? 'bg-tape-tint' : 'active:bg-primer',
                )}
              >
                <span className="w-10 shrink-0 pt-px text-secondary tabular-nums text-pencil">{formatClock(t.start_ms / 1000)}</span>
                <span className="min-w-0 flex-1">
                  {t.kind === 'location' ? (
                    <span className="block text-body font-semibold text-graphite">{t.text}</span>
                  ) : (
                    <span className={cx('block text-body', t.kind === 'other' ? 'text-pencil' : 'text-graphite')}>{t.text}</span>
                  )}
                  {t.kind === 'status_update' && t.claimed && (
                    <span className="mt-0.5 block text-secondary text-pencil">{CLAIM_WORDS[t.claimed]}</span>
                  )}
                </span>
                {numbers.length > 0 && (
                  <span className="flex shrink-0 gap-1 pt-0.5">
                    {numbers.map((n) => (
                      <TapeTab key={n} number={n} size="sm" decorative />
                    ))}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
