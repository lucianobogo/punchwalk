// Cards, section headers, key-value details, progress and skeletons: the quiet pieces around the tape tab.
import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from '../lib/cx';
import { StatusDot } from './Pill';

/** Sheet surface with a 1 px Hairline border and no shadow. `flagged` is the Hold-tinted card (no left border). */
export function Card({
  tone = 'default',
  padded = true,
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement> & { tone?: 'default' | 'flagged'; padded?: boolean }) {
  return (
    <div
      className={cx(
        'rounded-card border',
        tone === 'flagged' ? 'border-hold-border bg-hold-card' : 'border-hairline bg-sheet',
        padded && 'p-4',
        className,
      )}
      {...rest}
    />
  );
}

/** "Needs a look 2", "Bedroom 2". Sentence case, Pencil, never a small label above a heading. */
export function SectionHeader({
  children,
  count,
  tone = 'default',
  className,
  as: Tag = 'h2',
}: {
  children: ReactNode;
  count?: number;
  tone?: 'default' | 'hold';
  className?: string;
  as?: 'h2' | 'h3' | 'div';
}) {
  return (
    <Tag
      className={cx(
        'flex items-center gap-2 text-section font-medium',
        tone === 'hold' ? 'text-hold-ink' : 'text-pencil',
        className,
      )}
    >
      {tone === 'hold' && <StatusDot className="size-2.5" />}
      <span>{children}</span>
      {count != null && <span className="tabular-nums">{count}</span>}
    </Tag>
  );
}

/** Key-value list (item detail "Details"). */
export function DetailList({ rows, className }: { rows: { label: string; value: ReactNode }[]; className?: string }) {
  return (
    <Card padded={false} className={cx('px-4', className)}>
      <dl>
        {rows.map((r) => (
          <div key={r.label} className="flex min-h-12 items-center gap-4 border-b border-hairline py-3 last:border-b-0">
            <dt className="text-body text-pencil">{r.label}</dt>
            <dd className="ml-auto text-right text-body text-graphite">{r.value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

export function ProgressBar({
  value,
  label,
  tone = 'light',
  className,
}: {
  /** 0..1 */
  value: number;
  /** Accessible name, e.g. "Writing items". */
  label: string;
  tone?: 'light' | 'night';
  className?: string;
}) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      className={cx('h-1.5 overflow-hidden rounded-full', tone === 'night' ? 'bg-night-line' : 'bg-hairline', className)}
    >
      <div className="h-full rounded-full bg-tape transition-[width] duration-250 ease-out" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx('animate-pulse-soft rounded-thumb bg-track', className)} aria-hidden />;
}

/** Skeleton rows shaped like ListRow, with words for screen readers ("Loading the punch list"). */
export function SkeletonRows({ count = 4, label }: { count?: number; label: string }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-2">
      <span className="sr-only">{label}</span>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-card border border-hairline bg-sheet p-3">
          <Skeleton className="h-[66px] w-[88px] shrink-0" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-4 w-3/4 rounded-full" />
            <Skeleton className="h-3 w-full rounded-full" />
            <Skeleton className="h-3 w-1/2 rounded-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Messages for empty and error states, with one button that says what happens. */
export function EmptyState({
  title,
  body,
  action,
  className,
}: {
  title: string;
  body?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx('flex flex-col items-center px-6 py-12 text-center', className)}>
      <p className="max-w-[300px] text-card font-semibold text-graphite">{title}</p>
      {body && <p className="mt-2 max-w-[300px] text-body text-pencil">{body}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
