// Compact item rows (DESIGN.md "Punch list"): an 88 x 66 thumbnail with its tape tab on the corner,
// the title, up to two lines of summary in Pencil, an optional Safety pill, and a play button.
// About five fit on screen. The whole row opens item detail; the play button is its own target.
import { Play } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { cx } from '../lib/cx';
import { TapeTab, type TapeTabBadge, type TapeTabSize } from './TapeTab';

/** A thumbnail or photo with its tape tab stuck on the top-left corner (overlapping by ~7 px). */
export function EvidenceThumb({
  src,
  alt = '',
  number,
  badge,
  width = 88,
  height = 66,
  tabSize = 'sm',
  className,
}: {
  src?: string | null;
  alt?: string;
  number?: number | null;
  badge?: TapeTabBadge;
  width?: number | string;
  height?: number | string;
  tabSize?: TapeTabSize;
  className?: string;
}) {
  return (
    <span className={cx('relative block shrink-0', className)} style={{ width, height }}>
      {src ? (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          className="block size-full rounded-thumb bg-track object-cover"
        />
      ) : (
        <span className="flex size-full items-center justify-center rounded-thumb bg-track text-secondary text-pencil">
          No photo
        </span>
      )}
      {number != null && (
        <span className="absolute -top-[7px] -left-[7px] flex">
          <TapeTab number={number} size={tabSize} badge={badge} decorative />
        </span>
      )}
    </span>
  );
}

/** 34 px Tape-tint circle inside a 44 px hit area. */
export function PlayButton({
  label,
  onClick,
  tone = 'light',
  size = 34,
  className,
}: {
  /** e.g. "Play this moment, 0:14" */
  label: string;
  onClick?: () => void;
  tone?: 'light' | 'night';
  size?: 34 | 44 | 56;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className={cx(
        'hit-44 inline-flex shrink-0 items-center justify-center rounded-full transition-[filter] duration-150 active:brightness-95',
        tone === 'light' ? 'bg-tape-tint text-tape-ink' : 'bg-white text-night',
        className,
      )}
      style={{ width: size, height: size }}
    >
      <Play size={Math.round(size * 0.38)} fill="currentColor" strokeWidth={0} className="translate-x-px" aria-hidden />
    </button>
  );
}

export interface ListRowProps {
  /** Where tapping the row goes (item detail). */
  to: string;
  number: number;
  badge?: TapeTabBadge;
  thumbSrc?: string | null;
  title: string;
  /** Up to 2 lines (for Needs a look: why it needs a look). */
  summary?: ReactNode;
  /** e.g. <SeverityPill severity="safety" /> */
  pill?: ReactNode;
  /** Opens Play this moment. Omit to hide the button. */
  onPlay?: () => void;
  playLabel?: string;
  selected?: boolean;
  className?: string;
}

export function ListRow({
  to,
  number,
  badge,
  thumbSrc,
  title,
  summary,
  pill,
  onPlay,
  playLabel = 'Play this moment',
  selected,
  className,
}: ListRowProps) {
  return (
    <div
      className={cx(
        'relative flex items-center gap-3 rounded-card border border-hairline p-3 pr-2',
        selected ? 'bg-tape-tint' : 'bg-sheet',
        className,
      )}
    >
      <EvidenceThumb src={thumbSrc} number={number} badge={badge} />
      <div className="min-w-0 flex-1 py-0.5">
        <Link
          to={to}
          className="block truncate text-row font-semibold text-graphite after:absolute after:inset-0 after:rounded-card after:content-[''] focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-tape-ink"
        >
          <span className="sr-only">{`Item ${number}: `}</span>
          {title}
        </Link>
        {summary && <p className="mt-0.5 line-clamp-2 text-secondary text-pencil">{summary}</p>}
        {pill && <div className="mt-1.5">{pill}</div>}
      </div>
      {onPlay && <PlayButton label={playLabel} onClick={onPlay} className="relative z-10 mx-1.5" />}
    </div>
  );
}

/** Slim row: tape tab, title, trailing text ("1:21", "Signed off"). Used in walk detail and re-walk lists. */
export function MiniRow({
  number,
  badge,
  title,
  trailing,
  onClick,
  to,
  className,
}: {
  number: number;
  badge?: TapeTabBadge;
  title: string;
  trailing?: ReactNode;
  onClick?: () => void;
  to?: string;
  className?: string;
}) {
  const inner = (
    <>
      <TapeTab number={number} size="lg" badge={badge} decorative />
      <span className="min-w-0 flex-1 truncate text-left text-card text-graphite">
        <span className="sr-only">{`Item ${number}: `}</span>
        {title}
      </span>
      {trailing && <span className="shrink-0 text-body tabular-nums text-pencil">{trailing}</span>}
    </>
  );
  const cls = cx('flex min-h-14 w-full items-center gap-3 px-4 py-3', className);
  if (to) {
    return (
      <Link to={to} className={cls}>
        {inner}
      </Link>
    );
  }
  return (
    <button type="button" onClick={onClick} className={cls}>
      {inner}
    </button>
  );
}
