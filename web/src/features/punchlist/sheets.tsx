// Punch list sheets: a filter sheet per chip (checkable options with counts and "Show 3 items"),
// search + group + sort, and the project switcher behind the title.
import { Check, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { matchesFilters } from '../../api/filters';
import { SEVERITY_LABEL, TRADE_LABEL, type ItemOut, type ProjectOut, type Severity, type Trade } from '../../api/types';
import { Button, CheckOption, SegmentedControl, Sheet } from '../../components';
import { plural } from '../../lib/labels';
import { cx } from '../../lib/cx';
import { matchesSent, viewFilters, type Group, type ListView, type Sent } from './view';

export type Dimension = 'trade' | 'severity' | 'room';

export const DIMENSION_LABEL: Record<Dimension, string> = { trade: 'Trade', severity: 'Severity', room: 'Room' };

const SEVERITY_ORDER: Severity[] = ['safety', 'functional', 'cosmetic'];
const TRADE_ORDER = Object.keys(TRADE_LABEL) as Trade[];

export function optionLabel(dim: Dimension, value: string): string {
  if (dim === 'trade') return TRADE_LABEL[value as Trade] ?? value;
  if (dim === 'severity') return SEVERITY_LABEL[value as Severity] ?? value;
  return value;
}

/** Items in the view (segment, other filters, sent) — what the counts in the sheets describe. */
export function countMatching(all: ItemOut[], view: ListView): number {
  const f = viewFilters(view);
  return all.filter((i) => matchesFilters(i, f) && matchesSent(i, view.sent)).length;
}

/** Options for one dimension with counts, given every other filter in the view. */
export function facetOptions(all: ItemOut[], view: ListView, dim: Dimension, roomOrder: string[] = []) {
  const others: ListView = { ...view, filters: { ...view.filters, [dim]: undefined } };
  const f = viewFilters(others);
  const pool = all.filter((i) => matchesFilters(i, f) && matchesSent(i, view.sent));
  const counts = new Map<string, number>();
  for (const it of pool) {
    const v = dim === 'room' ? it.room : it[dim];
    if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  for (const v of view.filters[dim] ?? []) if (!counts.has(v)) counts.set(v, 0);
  let order: string[];
  if (dim === 'trade') order = TRADE_ORDER;
  else if (dim === 'severity') order = SEVERITY_ORDER;
  else order = [...roomOrder, ...[...counts.keys()].filter((r) => !roomOrder.includes(r))];
  return order.filter((v) => counts.has(v)).map((v) => ({ value: v, label: optionLabel(dim, v), count: counts.get(v)! }));
}

export function FilterSheet({
  dim,
  onClose,
  all,
  view,
  roomOrder,
  onApply,
}: {
  dim: Dimension | null;
  onClose: () => void;
  all: ItemOut[];
  view: ListView;
  roomOrder: string[];
  onApply: (next: ListView) => void;
}) {
  const [draft, setDraft] = useState<string[]>([]);
  useEffect(() => {
    if (dim) setDraft([...(view.filters[dim] ?? [])]);
  }, [dim, view]);

  const options = dim ? facetOptions(all, view, dim, roomOrder) : [];
  const draftView: ListView | null = dim
    ? { ...view, filters: { ...view.filters, [dim]: draft.length ? draft : undefined } }
    : null;
  const n = draftView ? countMatching(all, draftView) : 0;

  return (
    <Sheet
      open={dim != null}
      onOpenChange={(o) => !o && onClose()}
      title={dim ? DIMENSION_LABEL[dim] : 'Filter'}
      footer={
        <div className="flex gap-3">
          <Button variant="secondary" size="lg" onClick={() => setDraft([])} disabled={!draft.length}>
            Clear
          </Button>
          <Button
            size="lg"
            className="flex-1"
            onClick={() => {
              if (draftView) onApply(draftView);
              onClose();
            }}
          >
            {n === 0 ? 'No items match' : `Show ${plural(n, 'item')}`}
          </Button>
        </div>
      }
    >
      <div className="px-4" role="group" aria-label={dim ? `${DIMENSION_LABEL[dim]} options` : undefined}>
        {options.map((o) => (
          <CheckOption
            key={o.value}
            label={o.label}
            count={o.count}
            checked={draft.includes(o.value)}
            onChange={(c) => setDraft((d) => (c ? [...d, o.value] : d.filter((x) => x !== o.value)))}
          />
        ))}
        {!options.length && <p className="py-6 text-body text-pencil">Nothing to filter in this view.</p>}
      </div>
    </Sheet>
  );
}

const GROUP_OPTIONS: { value: Group; label: string }[] = [
  { value: 'room', label: 'Room' },
  { value: 'walk', label: 'Walk order' },
  { value: 'trade', label: 'Trade' },
];
const SORT_OPTIONS: { value: 'walk' | 'severity'; label: string }[] = [
  { value: 'walk', label: 'Walk order' },
  { value: 'severity', label: 'Severity' },
];
const SENT_OPTIONS: { value: Sent; label: string }[] = [
  { value: 'any', label: 'Any' },
  { value: 'yes', label: 'Sent' },
  { value: 'no', label: 'Not sent yet' },
];

export function SearchSheet({
  open,
  onClose,
  all,
  view,
  onApply,
}: {
  open: boolean;
  onClose: () => void;
  all: ItemOut[];
  view: ListView;
  onApply: (next: ListView) => void;
}) {
  const [draft, setDraft] = useState<ListView>(view);
  useEffect(() => {
    if (open) setDraft(view);
  }, [open, view]);
  const n = countMatching(all, draft);
  const apply = () => {
    onApply(draft);
    onClose();
  };

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Search, group and sort"
      footer={
        <Button size="lg" block onClick={apply}>
          {n === 0 ? 'No items match' : `Show ${plural(n, 'item')}`}
        </Button>
      }
    >
      <form
        className="flex flex-col gap-5 px-4 pb-2"
        onSubmit={(e) => {
          e.preventDefault();
          apply();
        }}
      >
        <label className="flex h-12 items-center gap-2 rounded-button border border-hairline bg-primer px-3 focus-within:border-tape-ink">
          <Search size={18} className="shrink-0 text-pencil" aria-hidden />
          <input
            type="search"
            enterKeyHint="search"
            value={draft.filters.q ?? ''}
            onChange={(e) => setDraft({ ...draft, filters: { ...draft.filters, q: e.target.value || undefined } })}
            placeholder="Search titles, rooms and quotes"
            aria-label="Search items"
            className="h-full min-w-0 flex-1 bg-transparent text-body text-graphite outline-none placeholder:text-pencil"
          />
        </label>
        <Field label="Group by">
          <SegmentedControl label="Group by" options={GROUP_OPTIONS} value={draft.group} onChange={(group) => setDraft({ ...draft, group })} />
        </Field>
        <Field label="Sort">
          <SegmentedControl
            label="Sort"
            options={SORT_OPTIONS}
            value={draft.filters.sort ?? 'walk'}
            onChange={(sort) => setDraft({ ...draft, filters: { ...draft.filters, sort: sort === 'walk' ? undefined : sort } })}
          />
        </Field>
        <Field label="Sent to the trade">
          <SegmentedControl label="Sent to the trade" options={SENT_OPTIONS} value={draft.sent} onChange={(sent) => setDraft({ ...draft, sent })} />
        </Field>
      </form>
    </Sheet>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-section font-medium text-pencil">{label}</span>
      {children}
    </div>
  );
}

export function ProjectSheet({
  open,
  onClose,
  projects,
  currentId,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  projects: ProjectOut[];
  currentId: string | undefined;
  onPick: (id: string) => void;
}) {
  const sorted = useMemo(() => projects, [projects]);
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="Projects">
      <ul className="px-4 pb-2" role="listbox" aria-label="Projects">
        {sorted.map((p) => {
          const selected = p.id === currentId;
          return (
            <li key={p.id} role="option" aria-selected={selected}>
              <button
                type="button"
                onClick={() => {
                  onPick(p.id);
                  onClose();
                }}
                className={cx(
                  'flex min-h-14 w-full items-center gap-3 border-b border-hairline py-3 text-left last:border-b-0',
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-row font-semibold text-graphite">{p.name}</span>
                  <span className="block text-secondary text-pencil">
                    {p.counts.all === 0 ? 'No items yet.' : `${p.counts.todo} to do, ${p.counts.signed_off} signed off.`}
                  </span>
                </span>
                {selected && <Check size={20} className="shrink-0 text-tape-ink" aria-hidden />}
              </button>
            </li>
          );
        })}
      </ul>
    </Sheet>
  );
}
