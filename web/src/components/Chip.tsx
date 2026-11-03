// Filter chips (DESIGN.md "Punch list"): Trade, Severity, Room. Each opens a sheet. Active chips fill
// with Tape tint and get a clear control.
import { ChevronDown, X } from 'lucide-react';
import { cx } from '../lib/cx';

export interface ChipProps {
  label: string;
  /** Shown after the label, e.g. "Electrical 3". */
  count?: number;
  active?: boolean;
  /** Opens the chip's sheet (adds a chevron). */
  onOpen?: () => void;
  /** Shown only when active: clears this filter. */
  onClear?: () => void;
  clearLabel?: string;
  className?: string;
}

export function Chip({ label, count, active = false, onOpen, onClear, clearLabel, className }: ChipProps) {
  return (
    <span
      className={cx(
        'inline-flex h-9 shrink-0 items-center rounded-full border text-body transition-colors duration-150',
        active ? 'border-tape-tint bg-tape-tint text-tape-ink' : 'border-hairline bg-sheet text-graphite',
        className,
      )}
    >
      <button
        type="button"
        onClick={onOpen}
        aria-haspopup={onOpen ? 'dialog' : undefined}
        aria-pressed={onOpen ? undefined : active}
        className={cx(
          'hit-44 inline-flex h-full items-center gap-1.5 rounded-full pl-3.5',
          active && onClear ? 'pr-1' : 'pr-3',
          active && 'font-medium',
        )}
      >
        <span className="whitespace-nowrap">{label}</span>
        {count != null && <span className="tabular-nums">{count}</span>}
        {onOpen && !(active && onClear) && <ChevronDown size={16} strokeWidth={2} aria-hidden />}
      </button>
      {active && onClear && (
        <button
          type="button"
          onClick={onClear}
          aria-label={clearLabel ?? `Clear ${label}`}
          className="hit-44 mr-1.5 inline-flex size-6 items-center justify-center rounded-full active:bg-white"
        >
          <X size={15} strokeWidth={2.25} aria-hidden />
        </button>
      )}
    </span>
  );
}

/** A checkable option inside a filter sheet: "Electrical 3". */
export function CheckOption({
  label,
  count,
  checked,
  onChange,
}: {
  label: string;
  count?: number;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex min-h-12 cursor-pointer items-center gap-3 border-b border-hairline last:border-b-0">
      <input
        type="checkbox"
        className="size-5 shrink-0 accent-tape-ink"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="flex-1 text-body text-graphite">{label}</span>
      {count != null && <span className="text-body tabular-nums text-pencil">{count}</span>}
    </label>
  );
}
