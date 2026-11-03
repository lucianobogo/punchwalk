// The live checklist card plus the "Items so far" strip (DESIGN.md "Record", 05-record-processing.png).
import { Check, CircleAlert } from 'lucide-react';
import { Link } from 'react-router';
import type { JobProgressOut } from '../../api/types';
import { Card, ProgressBar, SectionHeader, TapeTab } from '../../components';
import { formatDuration } from '../../lib/format';
import { cx } from '../../lib/cx';
import { checklistRows, type RowStatus } from './checklist';

function StatusIcon({ status }: { status: RowStatus }) {
  if (status === 'done') {
    return (
      <span className="flex size-6 items-center justify-center rounded-full bg-signed text-white">
        <Check size={15} strokeWidth={3.25} aria-hidden />
      </span>
    );
  }
  if (status === 'failed') return <CircleAlert size={24} strokeWidth={2.25} className="text-safety" aria-hidden />;
  if (status === 'running') {
    return (
      <svg viewBox="0 0 24 24" className="size-6 animate-spin [animation-duration:1.2s]" aria-hidden>
        <circle cx="12" cy="12" r="10" fill="none" stroke="var(--color-hairline)" strokeWidth="2.5" />
        <path d="M12 2 A10 10 0 0 1 21.5 9" fill="none" stroke="var(--color-tape)" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
    );
  }
  return <span className="block size-6 rounded-full border-[2.5px] border-hairline" aria-hidden />;
}

const STATUS_WORDS: Record<RowStatus, string> = {
  done: 'Done',
  running: 'In progress',
  pending: 'Waiting',
  failed: 'Failed',
};

export function ProcessingChecklist({ progress }: { progress: JobProgressOut }) {
  const rows = checklistRows(progress.stages);
  return (
    <Card padded={false} className="divide-y divide-hairline">
      {rows.map((r) => (
        <div key={r.key} className="flex gap-3.5 px-4 py-4">
          <div className="pt-px">
            <StatusIcon status={r.status} />
          </div>
          <div className="min-w-0 flex-1">
            <p className={cx('text-card font-semibold', r.status === 'pending' ? 'text-pencil' : 'text-graphite')}>
              <span className="sr-only">{STATUS_WORDS[r.status]}: </span>
              {r.title}
            </p>
            {r.detail && <p className={cx('mt-0.5 text-body', r.status === 'failed' ? 'text-safety' : 'text-pencil')}>{r.detail}</p>}
            {r.status === 'running' && r.progress != null && <ProgressBar value={r.progress} label={r.title} className="mt-2.5" />}
          </div>
          {r.durationMs != null && <span className="shrink-0 pt-0.5 text-secondary tabular-nums text-pencil">{formatDuration(r.durationMs)}</span>}
        </div>
      ))}
    </Card>
  );
}

/** Thumbnails of the items written so far, plus a "Writing N" tile while more are coming. */
export function ItemsSoFar({ progress }: { progress: JobProgressOut }) {
  const items = progress.items_so_far;
  const running = progress.status === 'running' || progress.status === 'queued';
  const next = items.length + 1;
  const more = running && (progress.items_expected == null || items.length < progress.items_expected);
  if (!items.length && !more) return null;
  return (
    <section aria-label="Items so far" className="mt-6">
      <SectionHeader className="mb-2.5">Items so far</SectionHeader>
      <ul className="-mx-4 flex gap-3 overflow-x-auto px-4 pt-2 pb-1 [scrollbar-width:none]">
        {items.map((it) => (
          <li key={it.id} className="relative w-[calc((100%-24px)/3)] shrink-0">
            <Link to={`/items/${it.id}`} className="block" aria-label={`Item ${it.number}: ${it.title}`}>
              {it.crop_url ? (
                <img src={it.crop_url} alt="" className="aspect-[4/3] w-full rounded-image bg-track object-cover" />
              ) : (
                <span className="block aspect-[4/3] w-full rounded-image bg-track" />
              )}
              <span className="absolute -top-1.5 -left-1.5 flex">
                <TapeTab number={it.number} size="sm" decorative />
              </span>
            </Link>
          </li>
        ))}
        {more && (
          <li className="w-[calc((100%-24px)/3)] shrink-0">
            <span className="flex aspect-[4/3] w-full items-center justify-center rounded-image bg-track text-body text-pencil">
              Writing {next}
            </span>
          </li>
        )}
      </ul>
    </section>
  );
}
