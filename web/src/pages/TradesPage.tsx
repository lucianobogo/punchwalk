// Trades (DESIGN.md "Trades"): each trade with what's to review, to send and sent, and the channel a send
// will use; each trade's screen with the items to review, the message exactly as it will be sent, what was
// sent before, and a button that names the outcome.
import { ChevronRight, Copy } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { useDispatchPreview, useDispatches, useTrades } from '../api/hooks';
import { useCurrentProject } from '../api/project';
import type { Channel, DispatchOut, DispatchPreviewOut, Trade, TradeSummaryOut } from '../api/types';
import {
  Button,
  Card,
  EmptyState,
  LargeTitleBar,
  PushedBar,
  SectionHeader,
  SegmentedControl,
  Skeleton,
  SkeletonRows,
  StatusDot,
  TapeTab,
  useToast,
} from '../components';
import {
  buildDispatch,
  channelChoices,
  countsSentence,
  dispatchText,
  headingFor,
  reviewLine,
  sendButtonLabel,
  sentSummary,
  sentWhere,
} from '../features/trades/dispatch';
import { copyText, useSendToTrade } from '../features/trades/useSend';
import { formatDateTime } from '../lib/format';
import { TRADE_LABEL } from '../lib/labels';
import { cx } from '../lib/cx';
import { ErrorState, ProjectGate } from './common';

// ------------------------------------------------------------------ chips

function CountChips({ t }: { t: Pick<TradeSummaryOut, 'to_review' | 'to_send' | 'sent'> }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {t.to_review > 0 && (
        <span className="inline-flex h-6 items-center gap-1 rounded-full border border-hold-border bg-hold-card px-2.5 text-secondary font-medium text-hold-ink">
          <StatusDot /> {t.to_review} to review
        </span>
      )}
      {t.to_send > 0 && (
        <span className="inline-flex h-6 items-center rounded-full bg-tape-tint px-2.5 text-secondary font-medium tabular-nums text-tape-ink">
          {t.to_send} to send
        </span>
      )}
      {t.sent > 0 && <span className="text-secondary tabular-nums text-pencil">{t.sent} sent</span>}
    </span>
  );
}

// ------------------------------------------------------------------ list

export function TradesPage() {
  const { projectId } = useCurrentProject();
  const trades = useTrades(projectId);
  const [active, caughtUp] = useMemo(() => {
    const all = [...(trades.data?.trades ?? [])];
    const byName = (a: TradeSummaryOut, b: TradeSummaryOut) => TRADE_LABEL[a.trade].localeCompare(TRADE_LABEL[b.trade]);
    const act = all
      .filter((t) => !t.caught_up)
      .sort((a, b) => b.to_review + b.to_send - (a.to_review + a.to_send) || byName(a, b));
    const done = all.filter((t) => t.caught_up).sort((a, b) => b.sent - a.sent || byName(a, b));
    return [act, done];
  }, [trades.data]);

  return (
    <>
      <LargeTitleBar title="Trades" />
      <div className="px-4 pt-4">
        {!projectId ? (
          <ProjectGate loadingLabel="Loading trades">{null}</ProjectGate>
        ) : trades.isLoading ? (
          <SkeletonRows label="Loading trades" count={4} />
        ) : trades.isError ? (
          <ErrorState error={trades.error} onRetry={() => trades.refetch()} />
        ) : !active.length && !caughtUp.length ? (
          <EmptyState title="No trades on this project yet." body="Trades show up here once a walk has items for them." />
        ) : (
          <>
            {active.length > 0 && (
              <Card padded={false} className="divide-y divide-hairline overflow-hidden">
                {active.map((t) => (
                  <TradeRow key={t.trade} t={t} />
                ))}
              </Card>
            )}
            {caughtUp.length > 0 && (
              <section aria-labelledby="caught-up-h" className={active.length ? 'mt-6' : undefined}>
                <SectionHeader as="h2" className="mb-2.5" count={caughtUp.length}>
                  <span id="caught-up-h">All caught up</span>
                </SectionHeader>
                <Card padded={false} className="divide-y divide-hairline overflow-hidden">
                  {caughtUp.map((t) => (
                    <TradeRow key={t.trade} t={t} />
                  ))}
                </Card>
              </section>
            )}
          </>
        )}
      </div>
    </>
  );
}

function TradeRow({ t }: { t: TradeSummaryOut }) {
  const company = t.company ?? t.contact?.company ?? null;
  return (
    <Link to={`/trades/${t.trade}`} className="flex min-h-16 items-center gap-3 px-4 py-3 active:bg-primer">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-row font-semibold text-graphite">{company ?? TRADE_LABEL[t.trade]}</span>
        <span className="block text-secondary text-pencil">{company ? TRADE_LABEL[t.trade] : 'No company yet'}</span>
        {(t.to_review > 0 || t.to_send > 0 || t.sent > 0) && (
          <span className="mt-1.5 block">
            <CountChips t={t} />
          </span>
        )}
        <span className="mt-1 block text-secondary text-pencil">{t.effective_channel_label}</span>
      </span>
      <ChevronRight size={20} className="shrink-0 text-pencil" aria-hidden />
    </Link>
  );
}

// ------------------------------------------------------------------ one trade

export function TradePage() {
  const { trade } = useParams() as { trade: Trade };
  const { projectId } = useCurrentProject();
  const preview = useDispatchPreview(projectId, trade);
  const p = preview.data;
  const company = p?.contact?.company ?? TRADE_LABEL[trade] ?? 'Trade';
  const counts = p ? { to_review: p.to_review.length, to_send: toSendLines(p).length, sent: p.already_sent.length } : null;

  return (
    <>
      <PushedBar backTo="/trades" backLabel="Trades" title={company} subtitle={counts ? countsSentence(counts) : undefined} />
      <div className="px-4 pt-4">
        {!projectId ? (
          <ProjectGate loadingLabel="Loading the message preview">{null}</ProjectGate>
        ) : preview.isLoading ? (
          <div role="status" aria-label="Loading the message preview">
            <Skeleton className="h-11 w-full rounded-button" />
            <Skeleton className="mt-4 h-64 w-full rounded-card" />
          </div>
        ) : preview.isError || !p ? (
          <ErrorState error={preview.error} onRetry={() => preview.refetch()} notFound="This trade isn’t on the project." />
        ) : (
          <TradeBody key={`${p.project_id}/${p.trade}`} p={p} company={company} />
        )}
      </div>
    </>
  );
}

/** To-send lines: the preview's lines minus anything already sent (those are listed under "Already sent"). */
function toSendLines(p: DispatchPreviewOut) {
  const sent = new Set(p.already_sent.map((s) => s.item_id));
  return p.lines.filter((l) => !sent.has(l.item_id));
}

function TradeBody({ p, company }: { p: DispatchPreviewOut; company: string }) {
  const history = useDispatches(p.project_id, p.trade);
  const sender = useSendToTrade();
  const choices = channelChoices(p);
  const [channel, setChannel] = useState<Channel>(p.effective_channel);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [resend, setResend] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!choices.some((c) => c.value === channel)) setChannel(p.effective_channel);
  }, [choices, channel, p.effective_channel]);

  const lines = toSendLines(p);
  const chosen = choices.find((c) => c.value === channel) ?? choices[0];
  const req = buildDispatch(chosen?.value ?? p.effective_channel, lines.map((l) => l.item_id), excluded, resend);
  const n = req.item_ids.length;
  const toggle = (set: Set<string>, id: string, on: boolean) => {
    const next = new Set(set);
    if (on) next.add(id);
    else next.delete(id);
    return next;
  };

  return (
    <>
      {p.to_review.length > 0 && <ReviewRow items={p.to_review} />}

      {choices.length > 1 ? (
        <div className={p.to_review.length ? 'mt-5' : undefined}>
          <SegmentedControl<Channel> label="Send by" value={chosen.value} onChange={setChannel} options={choices.map((c) => ({ value: c.value, label: c.label }))} />
          <p className="mt-2 text-secondary text-pencil">{chosen.value === p.effective_channel ? p.effective_channel_label : chosen.line}</p>
        </div>
      ) : (
        <p className={cx('text-body text-pencil', p.to_review.length > 0 && 'mt-5')}>{p.effective_channel_label}</p>
      )}

      {lines.length === 0 && p.already_sent.length === 0 ? (
        <EmptyState
          className="py-10"
          title={p.to_review.length ? `Nothing to send to ${company} yet.` : `Nothing open for ${company}.`}
          body={p.to_review.length ? 'Review the item above first.' : 'New items for this trade show up here after a walk.'}
        />
      ) : (
        <>
          {n === 0 && lines.length === 0 ? (
            <p className="mt-5 text-body text-pencil">Nothing new to send. Tick an item under Already sent to send it again.</p>
          ) : (
          <>
          <SectionHeader className="mt-5 mb-2.5">The message</SectionHeader>
          <Card padded={false} className="px-4 py-4">
            {chosen?.value === 'email' && <p className="mb-1 text-secondary text-pencil">Subject: {headingFor(p.subject, n, p.trade, lines.length)}</p>}
            <p className="text-card font-semibold text-graphite">{headingFor(p.heading, n, p.trade, lines.length)}</p>
            <ul className="mt-3 flex flex-col gap-4">
              {lines.map((l) => {
                const off = excluded.has(l.item_id);
                return (
                  <li key={l.item_id} className={cx('flex items-start gap-3', off && 'opacity-45')}>
                    <TapeTab number={l.number} size="sm" className="mt-0.5" decorative />
                    <div className="min-w-0 flex-1">
                      <p className="text-card text-graphite">{l.title}</p>
                      <p className="text-body text-pencil">{l.line}</p>
                      <Link to={l.url} className="-my-2.5 inline-flex min-h-11 items-center text-body text-tape-ink underline">
                        View item and video
                      </Link>
                    </div>
                    <IncludeBox
                      label={`Include item ${l.number}, ${l.title}`}
                      checked={!off}
                      onChange={(on) => setExcluded((s) => toggle(s, l.item_id, !on))}
                    />
                  </li>
                );
              })}
              {p.already_sent
                .filter((a) => resend.has(a.item_id))
                .map((a) => (
                  <li key={`re-${a.item_id}`} className="flex items-start gap-3">
                    <TapeTab number={a.number} size="sm" className="mt-0.5" decorative />
                    <div className="min-w-0 flex-1">
                      <p className="text-card text-graphite">{a.title}</p>
                      <p className="text-body text-pencil">Sent before. Going out again.</p>
                    </div>
                  </li>
                ))}
            </ul>
          </Card>
          </>
          )}

          {p.already_sent.length > 0 && (
            <section aria-labelledby="already-h" className="mt-5">
              <SectionHeader as="h2" className="mb-2.5" count={p.already_sent.length}>
                <span id="already-h">Already sent</span>
              </SectionHeader>
              <Card padded={false} className="divide-y divide-hairline overflow-hidden">
                {p.already_sent.map((a) => (
                  <div key={a.item_id} className="flex items-center gap-3 py-2 pr-3 pl-4">
                    <Link to={`/items/${a.item_id}`} className="flex min-h-12 min-w-0 flex-1 items-center gap-3">
                      <TapeTab number={a.number} size="sm" decorative />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-body text-graphite">
                          <span className="sr-only">{`Item ${a.number}: `}</span>
                          {a.title}
                        </span>
                        <span className="block text-secondary text-pencil">{sentWhere(a.channel, formatDateTime(a.sent_at))}</span>
                      </span>
                    </Link>
                    <IncludeBox
                      label={`Send item ${a.number} again`}
                      checked={resend.has(a.item_id)}
                      onChange={(on) => setResend((s) => toggle(s, a.item_id, on))}
                    />
                  </div>
                ))}
              </Card>
            </section>
          )}

          {(n > 0 || lines.length > 0) && (
          <Button
            size="lg"
            block
            className="mt-4"
            disabled={n === 0}
            busy={sender.isPending}
            busyLabel="Sending…"
            onClick={async () => {
              const out = await sender.run(p.project_id, p.trade, req.channel, req.item_ids, p.message_text);
              if (out?.ok) {
                setExcluded(new Set());
                setResend(new Set());
              }
            }}
          >
            {n === 0 ? 'Pick at least one item' : sendButtonLabel(req.channel, n, company)}
          </Button>
          )}
        </>
      )}

      <History company={company} dispatches={history.data?.dispatches ?? []} loading={history.isLoading} />
    </>
  );
}

function IncludeBox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (on: boolean) => void }) {
  return (
    <label className="-my-2.5 -mr-3 flex size-11 shrink-0 cursor-pointer items-center justify-center">
      <input type="checkbox" className="size-5 accent-tape-ink" checked={checked} aria-label={label} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

function ReviewRow({ items }: { items: DispatchPreviewOut['to_review'] }) {
  if (items.length === 1) {
    const it = items[0];
    return (
      <Link
        to={`/items/${it.item_id}`}
        className="flex min-h-14 items-center gap-3 rounded-card border border-hold-border bg-hold-card px-4 py-3 active:brightness-95"
      >
        <StatusDot className="size-2.5" />
        <span className="min-w-0 flex-1">
          <span className="block text-body font-medium text-hold-ink">{reviewLine(1)}</span>
          <span className="block truncate text-secondary text-graphite">
            {it.number}. {it.title}
          </span>
        </span>
        <ChevronRight size={20} className="shrink-0 text-hold-ink" aria-hidden />
      </Link>
    );
  }
  return (
    <div className="rounded-card border border-hold-border bg-hold-card">
      <p className="flex items-center gap-2.5 px-4 pt-3 pb-1 text-body font-medium text-hold-ink">
        <StatusDot className="size-2.5" /> {reviewLine(items.length)}
      </p>
      <ul>
        {items.map((it) => (
          <li key={it.item_id}>
            <Link to={`/items/${it.item_id}`} className="flex min-h-12 items-center gap-3 px-4 py-2 active:brightness-95">
              <TapeTab number={it.number} size="sm" decorative />
              <span className="min-w-0 flex-1 truncate text-body text-graphite">{it.title}</span>
              <ChevronRight size={18} className="shrink-0 text-hold-ink" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Never break a Slack channel name ("#punch-electrical") across lines. */
function KeepChannel({ text }: { text: string }) {
  return (
    <>
      {text.split(/(#[\w-]+)/).map((part, i) =>
        part.startsWith('#') ? (
          <span key={i} className="whitespace-nowrap">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
}

function History({ company, dispatches, loading }: { company: string; dispatches: DispatchOut[]; loading: boolean }) {
  const toast = useToast();
  if (loading) return null;
  if (!dispatches.length) return <p className="mt-5 text-center text-body text-pencil">Nothing sent to {company} yet.</p>;
  return (
    <section aria-labelledby="sent-h" className="mt-6">
      <SectionHeader as="h2" className="mb-2.5" count={dispatches.length}>
        <span id="sent-h">History</span>
      </SectionHeader>
      <Card padded={false} className="divide-y divide-hairline overflow-hidden">
        {dispatches.map((d) => {
          const text = dispatchText(d);
          return (
            <div key={d.id} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className={cx('text-body', d.ok ? 'text-graphite' : 'text-safety')}>
                  {d.ok ? <KeepChannel text={sentSummary(d)} /> : `Didn’t go through. ${d.error ?? ''}`.trim()}
                </p>
                <p className="text-secondary text-pencil">{formatDateTime(d.sent_at)}</p>
              </div>
              {text && (
                <Button
                  variant="quiet"
                  icon={<Copy size={16} aria-hidden />}
                  aria-label={`Copy the message from ${formatDateTime(d.sent_at)}`}
                  onClick={async () =>
                    toast.show((await copyText(text)) ? 'Message copied. Paste it anywhere.' : 'Copying isn’t allowed here. Open the item links instead.', {
                      tone: 'info',
                    })
                  }
                >
                  Copy
                </Button>
              )}
            </div>
          );
        })}
      </Card>
    </section>
  );
}
