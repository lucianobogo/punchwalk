// Walks list (DESIGN.md "Walks"): thumbnail, date and time, duration, items found, and status
// ("Processing", "Ready", or "Failed. Tap to retry"). Newest first, across projects.
import { ChevronRight } from 'lucide-react';
import { Link } from 'react-router';
import { useProgress, useWalks } from '../api/hooks';
import type { WalkOut } from '../api/types';
import { ButtonLink, EmptyState, LargeTitleBar, Pill, ProgressBar, SkeletonRows } from '../components';
import { formatDateTime, formatDuration } from '../lib/format';
import { WALK_KIND_LABEL, plural } from '../lib/labels';
import { ErrorState } from './common';

export default function WalksPage() {
  const walks = useWalks();
  const list = walks.data?.walks ?? [];
  return (
    <>
      <LargeTitleBar title="Walks" />
      <div className="px-4 pt-4">
        {walks.isLoading ? (
          <SkeletonRows label="Loading walks" count={3} />
        ) : walks.isError ? (
          <ErrorState error={walks.error} onRetry={() => walks.refetch()} />
        ) : !list.length ? (
          <EmptyState
            title="No walks yet."
            body="Point at each problem, say what’s wrong, and record the walk."
            action={<ButtonLink to="/record">Record a walk</ButtonLink>}
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {list.map((w) => (
              <li key={w.id}>
                <WalkRow walk={w} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

function walkHref(w: WalkOut): string {
  if (w.status === 'ready') return `/walks/${w.id}`;
  return `/record?walk=${w.id}`;
}

function WalkRow({ walk: w }: { walk: WalkOut }) {
  const busy = w.status === 'processing' || w.status === 'uploaded';
  const when = w.recorded_at ?? w.created_at;
  const facts = [
    formatDateTime(when),
    w.duration_s != null ? formatDuration(w.duration_s * 1000) : null,
    w.status === 'ready'
      ? w.kind === 'rewalk'
        ? `${plural(w.counts.items, 'item')} checked`
        : `${plural(w.counts.items, 'item')} found`
      : null,
  ].filter(Boolean);
  return (
    <Link to={walkHref(w)} className="flex items-center gap-3 rounded-card border border-hairline bg-sheet p-3 pr-2 active:bg-primer">
      {w.media.poster ? (
        <img src={w.media.poster} alt="" className="h-[66px] w-[88px] shrink-0 rounded-thumb bg-track object-cover" />
      ) : (
        <span className="h-[66px] w-[88px] shrink-0 rounded-thumb bg-track" />
      )}
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-row font-semibold text-graphite">{WALK_KIND_LABEL[w.kind]}</span>
          {busy && <Pill tone="open">Processing</Pill>}
          {w.status === 'failed' && <Pill tone="safety">Failed</Pill>}
        </span>
        <span className="block truncate text-secondary text-pencil">{w.project_name}</span>
        <span className="block text-secondary tabular-nums text-pencil">{facts.join(', ')}.</span>
        {busy && <ProcessingLine walkId={w.id} />}
        {w.status === 'failed' && <span className="mt-0.5 block text-secondary text-safety">Failed. Tap to retry.</span>}
      </span>
      <ChevronRight size={20} className="shrink-0 text-pencil" aria-hidden />
    </Link>
  );
}

/** "Writing items, 2 of 4 written" with a thin bar, from the job's progress. */
function ProcessingLine({ walkId }: { walkId: string }) {
  const p = useProgress(walkId, 4000);
  if (!p.data) return null;
  const running = p.data.stages.find((s) => s.status === 'running');
  return (
    <span className="mt-1.5 block">
      <ProgressBar value={p.data.progress} label="Processing" />
      {running && <span className="mt-1 block text-secondary text-pencil">{running.detail ?? running.label}</span>}
    </span>
  );
}
