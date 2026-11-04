// Item detail, DESIGN.md "Item detail" and docs/design/02-item-detail.png.
// Evidence (or before/after when signed off), title and AI summary, the action, the quote card with
// Play this moment, details, the verifier's check, history, and one status-dependent primary action.
import { Check, CircleAlert, MoreHorizontal, Pencil, Play, TriangleAlert, Wrench } from 'lucide-react';
import { useState } from 'react';
import { useLocation, useParams } from 'react-router';
import { useItem, useProject, useWalks } from '../api/hooks';
import type { ItemOut } from '../api/types';
import {
  ActionBar,
  Button,
  Card,
  DetailList,
  IconButton,
  Pill,
  PushedBar,
  SectionHeader,
  Sheet,
  SheetAction,
  Skeleton,
  StatusDot,
  StatusPill,
} from '../components';
import { BeforeAfter } from '../features/item/BeforeAfter';
import { EditSheet } from '../features/item/EditSheet';
import { EvidencePhoto } from '../features/item/EvidencePhoto';
import { checkerName, groundingLabel, historyText, moreActions, primaryAction, recipient, showsCheck } from '../features/item/actions';
import { useItemActions } from '../features/item/useItemActions';
import { PlayMomentSheet } from '../features/play/PlayMomentSheet';
import { formatClock, formatDateTime, formatShortDate } from '../lib/format';
import { SEVERITY_LABEL, TRADE_LABEL, WALK_KIND_LABEL, capitalize, statusDetail } from '../lib/labels';
import { cx } from '../lib/cx';
import { ErrorState } from './common';

export default function ItemDetailPage() {
  const { id } = useParams();
  const location = useLocation();
  const backTo = (location.state as { from?: string } | null)?.from ?? '/';
  const item = useItem(id);
  const [sheet, setSheet] = useState<ItemSheet>(null);

  return (
    <>
      <PushedBar
        backTo={backTo}
        backLabel="Punch list"
        actions={
          item.data ? (
            <IconButton label={`Edit item ${item.data.number}`} onClick={() => setSheet('edit')}>
              <Pencil size={22} strokeWidth={2} className="text-tape-ink" aria-hidden />
            </IconButton>
          ) : null
        }
      />
      {item.isLoading ? (
        <div className="px-4 pt-2" role="status" aria-label="Loading the item">
          <Skeleton className="aspect-[4/3] w-full rounded-card" />
          <Skeleton className="mt-5 h-7 w-3/4 rounded-full" />
          <Skeleton className="mt-3 h-4 w-full rounded-full" />
          <Skeleton className="mt-2 h-4 w-2/3 rounded-full" />
        </div>
      ) : item.isError || !item.data ? (
        <div className="px-4">
          <ErrorState error={item.error} onRetry={() => item.refetch()} notFound="This item isn’t here anymore." />
        </div>
      ) : (
        <ItemBody item={item.data} sheet={sheet} setSheet={setSheet} />
      )}
    </>
  );
}

type ItemSheet = 'more' | 'edit' | 'play' | 'dismiss' | null;

function ItemBody({ item, sheet, setSheet }: { item: ItemOut; sheet: ItemSheet; setSheet: (s: ItemSheet) => void }) {
  const project = useProject(item.project_id);
  const walks = useWalks(item.project_id);
  const contact = project.data?.trades.find((t) => t.trade === item.trade) ?? null;
  const company = recipient(item, contact);
  const walk = walks.data?.walks.find((w) => w.id === item.origin_walk_id);
  const { busy, runPrimary, runMore, saveEdits } = useItemActions(item, contact);
  const badge = item.status === 'needs_review' ? 'hold' : item.status === 'closed' ? 'signed' : null;
  const primary = primaryAction(item.status, company);
  const more = moreActions(item, company);
  const closure = item.closure;
  const showSlider = item.status === 'closed' && closure?.before_image_url && closure.after_image_url;
  const quoteAt = item.quote_start_s ?? item.video_start_s;
  const v = item.verification;

  const details = [
    { label: 'Room', value: item.room ?? 'Not set' },
    ...(item.location_detail ? [{ label: 'Where', value: capitalize(item.location_detail) }] : []),
    {
      label: 'Trade',
      value: (
        <span className="flex flex-col items-end">
          <span>{TRADE_LABEL[item.trade]}</span>
          {contact && <span className="text-secondary text-pencil">{contact.company}</span>}
        </span>
      ),
    },
    { label: 'Severity', value: SEVERITY_LABEL[item.severity] },
    { label: 'Status', value: statusDetail(item) },
    ...(walk
      ? [{ label: 'Walk', value: `${WALK_KIND_LABEL[walk.kind]}, ${formatShortDate(walk.recorded_at ?? walk.created_at)}` }]
      : []),
    ...(item.evidence ? [{ label: 'Found by', value: groundingLabel(item.evidence.grounding) }] : []),
  ];

  return (
    <div className="pb-[calc(96px+var(--sab))]">
      <div className="pt-4">
        {showSlider ? (
          <BeforeAfter
            before={closure!.before_image_url!}
            after={closure!.after_image_url!}
            beforeLabel={formatShortDate(item.created_at)}
            afterLabel={closure!.decided_at ? `${formatShortDate(closure!.decided_at)}, re-walk` : 'Re-walk'}
            number={item.number}
            badge={badge}
            title={item.title}
          />
        ) : item.evidence ? (
          <EvidencePhoto evidence={item.evidence} number={item.number} badge={badge} alt={`Photo of item ${item.number}: ${item.title}`} />
        ) : null}
      </div>

      <div className="px-4">
        <div className={cx('flex flex-wrap items-center gap-2', item.evidence || showSlider ? 'mt-5' : 'mt-2')}>
          {item.status === 'closed' && <StatusPill status={item.status} />}
          {item.severity === 'safety' && (
            <Pill tone="safety" icon={<TriangleAlert size={14} strokeWidth={2.25} aria-hidden />}>
              Safety
            </Pill>
          )}
        </div>
        <h1 className={cx('text-title font-semibold font-expanded-title text-graphite', item.status === 'closed' || item.severity === 'safety' ? 'mt-2' : '')}>
          {item.title}
        </h1>
        <p className="mt-1.5 text-body text-graphite">{item.description}</p>

        {item.status === 'needs_review' && item.review_reason && (
          <Card tone="flagged" className="mt-4">
            <SectionHeader tone="hold" as="div">
              Needs a look
            </SectionHeader>
            <p className="mt-1.5 text-body text-graphite">{item.review_reason}</p>
          </Card>
        )}

        <p className="mt-4 flex items-start gap-2.5 text-card font-semibold text-graphite">
          <Wrench size={20} strokeWidth={2} className="mt-0.5 shrink-0" aria-hidden />
          <span>{item.action}</span>
        </p>

        {item.video_url && (
          <button
            type="button"
            onClick={() => setSheet('play')}
            className="mt-4 flex w-full items-center gap-3 rounded-card border border-hairline bg-sheet p-4 text-left active:bg-primer"
          >
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-tape-tint text-tape-ink" aria-hidden>
              <Play size={14} fill="currentColor" strokeWidth={0} className="translate-x-px" />
            </span>
            <span className="min-w-0 flex-1">
              {item.quote ? (
                <span className="block text-body text-graphite">“{item.quote}”</span>
              ) : (
                <span className="block text-body text-pencil">Nobody described this one out loud.</span>
              )}
              <span className="mt-0.5 block text-secondary tabular-nums text-pencil">Play this moment, {formatClock(quoteAt)}</span>
            </span>
          </button>
        )}

        <DetailList rows={details} className="mt-4" />

        {showsCheck(item) && (
          <>
            <SectionHeader className="mt-6 mb-2.5">Checked</SectionHeader>
            <Card className="flex items-start gap-3">
              {v == null ? (
                <p className="text-body text-pencil">Not checked yet.</p>
              ) : (
                <>
                  {v.consistent ? (
                    <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-signed text-white" aria-hidden>
                      <Check size={13} strokeWidth={3.25} />
                    </span>
                  ) : (
                    <CircleAlert size={21} strokeWidth={2.25} className="mt-px shrink-0 text-hold-ink" aria-hidden />
                  )}
                  <span className="min-w-0">
                    <span className="block text-body text-graphite">
                      {v.consistent ? 'Matches the photo and what was said.' : (v.note ?? "Doesn't match the photo or what was said.")}
                    </span>
                    {!v.consistent && v.issues.length > 0 && (
                      <ul className="mt-1 list-disc pl-4 text-secondary text-pencil">
                        {v.issues.map((i) => (
                          <li key={i}>{i}</li>
                        ))}
                      </ul>
                    )}
                    <span className="mt-0.5 block text-secondary text-pencil">Checked by {checkerName(v.provider)}</span>
                  </span>
                </>
              )}
            </Card>
          </>
        )}

        {item.history.length > 0 && (
          <>
            <SectionHeader className="mt-6 mb-2.5">History</SectionHeader>
            <ol className="relative ml-1.5 border-l border-hairline">
              {[...item.history].reverse().map((h, i) => (
                <li key={`${h.at}-${i}`} className="relative pb-4 pl-5 last:pb-0">
                  <span
                    className={cx('absolute top-1.5 -left-[5px] size-[9px] rounded-full ring-2 ring-primer', h.actor === 'human' ? 'bg-tape-ink' : 'bg-graphite')}
                    aria-hidden
                  />
                  <p className="text-body text-graphite">{historyText(h)}</p>
                  <p className="text-secondary text-pencil">{formatDateTime(h.at)}</p>
                </li>
              ))}
            </ol>
          </>
        )}
      </div>

      <ActionBar>
        <Button
          size="lg"
          className={cx('min-w-0 flex-1 whitespace-nowrap', primary.label.length > 24 && 'text-body!')}
          busy={busy}
          busyLabel="Saving…"
          onClick={() => runPrimary(primary.kind)}
        >
          {primary.label}
        </Button>
        <IconButton label="More actions" tone="bordered" square size={48} onClick={() => setSheet('more')}>
          <MoreHorizontal size={22} aria-hidden />
        </IconButton>
      </ActionBar>

      <Sheet open={sheet === 'more'} onOpenChange={(o) => !o && setSheet(null)} title={`Item ${item.number}`}>
        {more.map((a) => (
          <SheetAction
            key={a.kind}
            tone={a.kind === 'dismiss' ? 'danger' : 'default'}
            onClick={() => {
              if (a.kind === 'dismiss') return setSheet('dismiss');
              setSheet(a.kind === 'edit' ? 'edit' : null);
              if (a.kind !== 'edit') runMore(a.kind);
            }}
          >
            {a.label}
          </SheetAction>
        ))}
      </Sheet>
      <Sheet
        open={sheet === 'dismiss'}
        onOpenChange={(o) => !o && setSheet(null)}
        title={`Dismiss item ${item.number}?`}
        footer={
          <div className="flex gap-3">
            <Button variant="secondary" size="lg" className="flex-1" onClick={() => setSheet(null)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              size="lg"
              className="flex-1"
              onClick={() => {
                setSheet(null);
                runMore('dismiss');
              }}
            >
              Dismiss item
            </Button>
          </div>
        }
      >
        <p className="flex items-start gap-2 px-4 pb-2 text-body text-graphite">
          <StatusDot className="mt-2" />
          It leaves the punch list and won’t be sent to anyone. The walk’s video and transcript keep it.
        </p>
      </Sheet>
      <EditSheet
        item={item}
        rooms={project.data?.rooms ?? []}
        open={sheet === 'edit'}
        onClose={() => setSheet(null)}
        onSave={saveEdits}
        saving={busy}
      />
      <PlayMomentSheet item={item} open={sheet === 'play'} onOpenChange={(o) => !o && setSheet(null)} />
    </div>
  );
}
