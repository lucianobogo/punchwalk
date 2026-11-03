// Edit an item: title, action, trade, severity, room. Saves only what changed; every change is a
// human override and shows up in History.
import { useEffect, useState, type ReactNode } from 'react';
import { SEVERITY_LABEL, TRADE_LABEL, type ItemOut, type ItemPatch, type Severity, type Trade } from '../../api/types';
import { Button, SegmentedControl, Sheet } from '../../components';

interface Draft {
  title: string;
  action: string;
  trade: Trade;
  severity: Severity;
  room: string;
}

const draftOf = (i: ItemOut): Draft => ({
  title: i.title,
  action: i.action,
  trade: i.trade,
  severity: i.severity,
  room: i.room ?? '',
});

/** Only the fields that changed. */
export function diffPatch(item: ItemOut, d: Draft): ItemPatch {
  const p: ItemPatch = {};
  if (d.title.trim() && d.title.trim() !== item.title) p.title = d.title.trim();
  if (d.action.trim() && d.action.trim() !== item.action) p.action = d.action.trim();
  if (d.trade !== item.trade) p.trade = d.trade;
  if (d.severity !== item.severity) p.severity = d.severity;
  if ((d.room || null) !== item.room && d.room) p.room = d.room;
  return p;
}

const inputCls =
  'h-12 w-full rounded-button border border-hairline bg-sheet px-3 text-body text-graphite outline-none focus:border-tape-ink';

export function EditSheet({
  item,
  rooms,
  open,
  onClose,
  onSave,
  saving,
}: {
  item: ItemOut;
  rooms: string[];
  open: boolean;
  onClose: () => void;
  onSave: (p: ItemPatch) => Promise<boolean>;
  saving: boolean;
}) {
  const [d, setD] = useState<Draft>(() => draftOf(item));
  useEffect(() => {
    if (open) setD(draftOf(item));
  }, [open, item]);
  const patch = diffPatch(item, d);
  const changed = Object.keys(patch).length > 0;
  const roomOptions = rooms.includes(d.room) || !d.room ? rooms : [d.room, ...rooms];

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={`Edit item ${item.number}`}
      footer={
        <Button
          size="lg"
          block
          disabled={!changed}
          busy={saving}
          busyLabel="Saving…"
          onClick={async () => {
            if (await onSave(patch)) onClose();
          }}
        >
          {changed ? 'Save changes' : 'No changes yet'}
        </Button>
      }
    >
      <div className="flex flex-col gap-4 px-4 pb-2">
        <Field label="Title" htmlFor="edit-title">
          <input id="edit-title" className={inputCls} value={d.title} onChange={(e) => setD({ ...d, title: e.target.value })} />
        </Field>
        <Field label="What to do" htmlFor="edit-action">
          <input id="edit-action" className={inputCls} value={d.action} onChange={(e) => setD({ ...d, action: e.target.value })} />
        </Field>
        <Field label="Trade" htmlFor="edit-trade">
          <select id="edit-trade" className={inputCls} value={d.trade} onChange={(e) => setD({ ...d, trade: e.target.value as Trade })}>
            {(Object.keys(TRADE_LABEL) as Trade[]).map((t) => (
              <option key={t} value={t}>
                {TRADE_LABEL[t]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Severity">
          <SegmentedControl
            label="Severity"
            value={d.severity}
            onChange={(severity) => setD({ ...d, severity })}
            options={(['cosmetic', 'functional', 'safety'] as Severity[]).map((s) => ({ value: s, label: SEVERITY_LABEL[s] }))}
          />
        </Field>
        <Field label="Room" htmlFor="edit-room">
          <select id="edit-room" className={inputCls} value={d.room} onChange={(e) => setD({ ...d, room: e.target.value })}>
            {!d.room && <option value="">Choose a room</option>}
            {roomOptions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </Field>
      </div>
    </Sheet>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-section font-medium text-pencil">
        {label}
      </label>
      {children}
    </div>
  );
}
