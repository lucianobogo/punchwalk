// "To do 6 / Signed off 6 / All 12", "Items so far / Transcript", "Slack / Email / Copy link",
// "First walk / Re-walk". A radio group: arrow keys move, the selected segment is white on the track.
import { useRef, type KeyboardEvent } from 'react';
import { cx } from '../lib/cx';

export interface Segment<T extends string> {
  value: T;
  label: string;
  count?: number;
  disabled?: boolean;
}

export interface SegmentedControlProps<T extends string> {
  options: Segment<T>[];
  value: T;
  onChange: (value: T) => void;
  /** Accessible name for the group. */
  label: string;
  className?: string;
}

export function SegmentedControl<T extends string>({ options, value, onChange, label, className }: SegmentedControlProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKey(e: KeyboardEvent, index: number) {
    const dir = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    for (let step = 1; step <= options.length; step++) {
      const i = (index + dir * step + options.length) % options.length;
      if (!options[i].disabled) {
        onChange(options[i].value);
        refs.current[i]?.focus();
        return;
      }
    }
  }

  return (
    <div role="radiogroup" aria-label={label} className={cx('flex h-11 rounded-button bg-track p-1', className)}>
      {options.map((o, i) => {
        const selected = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={o.disabled}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKey(e, i)}
            className={cx(
              'hit-44 flex flex-1 items-center justify-center gap-1 rounded-control px-2 text-body whitespace-nowrap',
              'transition-colors duration-150 ease-out disabled:opacity-50',
              selected ? 'bg-sheet font-medium text-graphite' : 'text-pencil',
            )}
          >
            <span>{o.label}</span>
            {o.count != null && <span className="tabular-nums">{o.count}</span>}
          </button>
        );
      })}
    </div>
  );
}
