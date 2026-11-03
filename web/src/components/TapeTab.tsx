// The signature component (DESIGN.md "Signature component: the tape tab"): the item number in
// graphite marker on a strip of light painter's-tape blue, rotated -3 deg, with ragged ends.
import { Check } from 'lucide-react';
import { cx } from '../lib/cx';

/** The path used in the renders, on a 40 x 24 viewBox. */
export const TAPE_PATH = 'M3 0H37L39 3L37 6L40 9L37 12L39 15L37 18L40 21L37 24H3L1 21L3 18L0 15L3 12L1 9L3 6L0 3Z';

export type TapeTabSize = 'lg' | 'sm';
export type TapeTabBadge = 'hold' | 'signed' | null;

const SIZES: Record<TapeTabSize, { w: number; h: number; text: string; dot: number; check: number }> = {
  lg: { w: 40, h: 24, text: 'text-tape-lg', dot: 12, check: 18 },
  sm: { w: 32, h: 20, text: 'text-tape-sm', dot: 10, check: 16 },
};

const BADGE_WORDS: Record<'hold' | 'signed', string> = { hold: 'needs a look', signed: 'signed off' };

export interface TapeTabProps {
  /** Item number: shown as-is, no "#" and no leading zeros. */
  number?: number | null;
  /** 40 x 24 for photos, video and headers; 32 x 20 for rows, thumbnails and message previews. */
  size?: TapeTabSize;
  /** Status badge on the top-right corner. The status must also appear in words elsewhere. */
  badge?: TapeTabBadge;
  /** Replace the number with a graphite check (the logo). */
  check?: boolean;
  /** Pressed on (scale 1.06 -> 1, 180 ms): the walk-detail moment. */
  pressOn?: boolean;
  /** Hide from assistive tech when the number is already read out nearby. */
  decorative?: boolean;
  className?: string;
}

export function TapeTab({
  number,
  size = 'lg',
  badge = null,
  check = false,
  pressOn = false,
  decorative = false,
  className,
}: TapeTabProps) {
  const s = SIZES[size];
  const digits = number == null ? 1 : String(Math.abs(Math.trunc(number))).length;
  const width = digits >= 3 ? Math.round(s.w * 1.3) : s.w;
  const label = check
    ? undefined
    : `Item ${number ?? ''}${badge ? `, ${BADGE_WORDS[badge]}` : ''}`.trim();

  return (
    <span
      className={cx('relative inline-flex shrink-0 select-none', pressOn && 'animate-press-on', className)}
      style={{ width, height: s.h }}
      role={decorative || check ? undefined : 'img'}
      aria-label={decorative ? undefined : label}
      aria-hidden={decorative || check ? true : undefined}
      data-tape-tab={number ?? ''}
    >
      <span className="absolute inset-0 -rotate-3">
        <svg
          viewBox="0 0 40 24"
          width={width}
          height={s.h}
          preserveAspectRatio="none"
          className="absolute inset-0 block"
          aria-hidden
        >
          <path d={TAPE_PATH} className="fill-tape-tab" />
        </svg>
        <span
          className={cx(
            'absolute inset-0 flex items-center justify-center font-expanded font-bold tabular-nums text-graphite',
            s.text,
          )}
        >
          {check ? <Check size={s.h - 6} strokeWidth={3.25} aria-hidden /> : number}
        </span>
      </span>
      {badge === 'hold' && (
        <span
          className="absolute rounded-full bg-hold ring-2 ring-sheet"
          style={{ width: s.dot, height: s.dot, top: -s.dot / 2 + 1, right: -s.dot / 2 + 1 }}
          aria-hidden
        />
      )}
      {badge === 'signed' && (
        <span
          className="absolute flex items-center justify-center rounded-full bg-signed text-white ring-2 ring-sheet"
          style={{ width: s.check, height: s.check, top: -s.check / 2 + 1, right: -s.check / 2 + 1 }}
          aria-hidden
        >
          <Check size={s.check - 6} strokeWidth={3.5} />
        </span>
      )}
    </span>
  );
}

/** Logo mark: the tape strip with a graphite check. */
export function TapeLogo({ className }: { className?: string }) {
  return <TapeTab check size="lg" className={className} />;
}
