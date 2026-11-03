// Trades (DESIGN.md "Trades", 06-send-to-trade.png): the list of trades with their company and open
// count, and each trade's screen with the channel, the message exactly as it will be sent, the send
// button, and what was sent before (the Outbox, for trades with no channel set up).
import { ChevronRight, Copy, Hash, Inbox, Mail } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { useDispatchPreview, useDispatches, useTrades } from '../api/hooks';
import { useCurrentProject } from '../api/project';
import type { Channel, DispatchOut, Trade, TradeSummaryOut } from '../api/types';
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
  TapeTab,
  useToast,
} from '../components';
import {
  buildDispatch,
  channelNote,
  channelOptions,
  destinationLine,
  dispatchText,
  headingFor,
  sendButtonLabel,
} from '../features/trades/dispatch';
import { copyText, useSendToTrade } from '../features/trades/useSend';
import { formatDateTime, formatShortDate } from '../lib/format';
import { TRADE_LABEL, plural } from '../lib/labels';
import { cx } from '../lib/cx';
import { ErrorState } from './common';

const CHANNEL_ICON: Record<Channel, typeof Hash> = { slack: Hash, email: Mail, outbox: Inbox };
/** In the list, the Outbox is called the Outbox (the picker's "Copy link" is the action). */
const CHANNEL_NAME: Record<Channel, string> = { slack: 'Slack', email: 'Email', outbox: 'Outbox' };

function preferredChannel(t: TradeSummaryOut): Channel {
  if (t.contact?.has_slack_webhook) return 'slack';
  if (t.contact?.email) return 'email';
  return 'outbox';
}

// ------------------------------------------------------------------ list

export function TradesPage() {
  const { projectId } = useCurrentProject();
  const trades = useTrades(projectId);
  const list = useMemo(
    () =>
      [...(trades.data?.trades ?? [])].sort(
        (a, b) =>
          b.open_count - a.open_count || b.needs_review_count - a.needs_review_count || TRADE_LABEL[a.trade].localeCompare(TRADE_LABEL[b.trade]),
      ),
    [trades.data],
  );

  return (
    <>
      <LargeTitleBar title="Trades" />
      <div className="px-4 pt-4">
        {trades.isLoading || !projectId ? (
          <SkeletonRows label="Loading trades" count={4} />
        ) : trades.isError ? (
          <ErrorState error={trades.error} onRetry={() => trades.refetch()} />
        ) : !list.length ? (
          <EmptyState title="No trades on this project yet." body="Trades show up here once a walk has items for them." />
        ) : (
          <Card padded={false} className="divide-y divide-hairline overflow-hidden">
            {list.map((t) => (
              <TradeRow key={t.trade} t={t} />
            ))}
          </Card>
        )}
      </div>
    </>
  );
}

function TradeRow({ t }: { t: TradeSummaryOut }) {
  const ch = preferredChannel(t);
  const Icon = CHANNEL_ICON[ch];
  const counts = [
    t.open_count ? plural(t.open_count, 'open item') : 'Nothing open',
    t.needs_review_count ? `${t.needs_review_count} ${t.needs_review_count === 1 ? 'needs' : 'need'} a look` : null,
  ].filter(Boolean);
  return (
    <Link to={`/trades/${t.trade}`} className="flex min-h-16 items-center gap-3 px-4 py-3 active:bg-primer">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-row font-semibold text-graphite">{t.contact?.company ?? TRADE_LABEL[t.trade]}</span>
        <span className="block text-secondary text-pencil">
          {TRADE_LABEL[t.trade]}. {counts.join(', ')}.
        </span>
        <span className="mt-0.5 flex items-center gap-1.5 text-secondary text-pencil">
          <Icon size={13} aria-hidden />
          <span>
            {CHANNEL_NAME[ch]}
            {t.last_sent_at ? `, last sent ${formatShortDate(t.last_sent_at)}` : ', nothing sent yet'}
          </span>
        </span>
      </span>
      {t.open_count > 0 && (
        <span className="flex h-7 min-w-7 items-center justify-center rounded-full bg-tape-tint px-2 text-secondary font-semibold tabular-nums text-tape-ink" aria-hidden>
          {t.open_count}
        </span>
      )}
      <ChevronRight size={20} className="shrink-0 text-pencil" aria-hidden />
    </Link>
  );
}

// ------------------------------------------------------------------ one trade

export function TradePage() {
  const { trade } = useParams() as { trade: Trade };
  const { projectId } = useCurrentProject();
  const preview = useDispatchPreview(projectId, trade);
  const history = useDispatches(projectId, trade);
  const sender = useSendToTrade();
  const [channel, setChannel] = useState<Channel | null>(null);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());

  const p = preview.data;
  useEffect(() => {
    if (p && channel == null) setChannel(p.channel);
  }, [p, channel]);

  const company = p?.contact?.company ?? TRADE_LABEL[trade] ?? 'Trade';
  const options = p ? channelOptions(p) : [];
  const chosen = options.find((o) => o.value === (channel ?? p?.channel)) ?? options[0];
  const req = p && chosen ? buildDispatch(chosen.value, p, excluded) : null;
  const n = req?.item_ids.length ?? 0;
  const openCount = p?.lines.length ?? 0;

  return (
    <>
      <PushedBar
        backTo="/trades"
        backLabel="Trades"
        title={company}
        subtitle={p ? `${TRADE_LABEL[trade]}. ${plural(openCount, 'open item')}.` : undefined}
      />
      <div className="px-4 pt-4">
        {preview.isLoading || !projectId ? (
          <div role="status" aria-label="Loading the message preview">
            <Skeleton className="h-11 w-full rounded-button" />
            <Skeleton className="mt-4 h-64 w-full rounded-card" />
          </div>
        ) : preview.isError || !p || !chosen ? (
          <ErrorState error={preview.error} onRetry={() => preview.refetch()} />
        ) : (
          <>
            <SegmentedControl<Channel>
              label="Send by"
              value={chosen.value}
              onChange={setChannel}
              options={options.map((o) => ({ value: o.value, label: o.label, disabled: o.disabled }))}
            />
            <ChannelNote
              text={channelNote(p, chosen)}
              // The API's reason already explains the default; list the unavailable channels only otherwise.
              reasons={p.channel_reason && chosen.value === p.channel ? [] : options.filter((o) => o.disabled && o.reason).map((o) => o.reason!)}
            />

            {openCount === 0 ? (
              <EmptyState className="py-10" title={`Nothing open for ${company}.`} body="New items for this trade show up here after a walk." />
            ) : (
              <>
                <SectionHeader className="mt-5 mb-2.5">{destinationLine(chosen.value, chosen.destination)}</SectionHeader>
                <Card padded={false} className="px-4 py-4">
                  {chosen.value === 'email' && <p className="mb-1 text-secondary text-pencil">Subject: {headingFor(p.subject, n, trade, openCount)}</p>}
                  <p className="text-card font-semibold text-graphite">{headingFor(p.heading, n, trade, openCount)}</p>
                  <ul className="mt-3 flex flex-col gap-4">
                    {p.lines.map((l) => {
                      const off = excluded.has(l.item_id);
                      return (
                        <li key={l.item_id} className={cx('flex items-start gap-3', off && 'opacity-45')}>
                          <TapeTab number={l.number} size="sm" className="mt-0.5" decorative />
                          <div className="min-w-0 flex-1">
                            <p className="text-card text-graphite">{l.title}</p>
                            <p className="text-body text-pencil">{l.line}</p>
                            <Link to={l.url} className="text-body text-tape-ink underline">
                              View item and video
                            </Link>
                          </div>
                          {openCount > 1 && (
                            <label className="-my-2.5 -mr-3 flex size-11 shrink-0 cursor-pointer items-center justify-center">
                              <input
                                type="checkbox"
                                className="size-5 accent-tape-ink"
                                checked={!off}
                                aria-label={`Include item ${l.number}, ${l.title}`}
                                onChange={(e) =>
                                  setExcluded((s) => {
                                    const next = new Set(s);
                                    if (e.target.checked) next.delete(l.item_id);
                                    else next.add(l.item_id);
                                    return next;
                                  })
                                }
                              />
                            </label>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </Card>
                <Button
                  size="lg"
                  block
                  className="mt-4"
                  disabled={n === 0}
                  busy={sender.isPending}
                  busyLabel="Sending…"
                  onClick={async () => {
                    if (!req) return;
                    const out = await sender.run(p.project_id, trade, req.channel, req.item_ids, p.message_text);
                    if (out?.ok) setExcluded(new Set());
                  }}
                >
                  {n === 0 ? 'Pick at least one item' : sendButtonLabel(chosen.value, n, company)}
                </Button>
              </>
            )}

            <History company={company} dispatches={history.data?.dispatches ?? []} loading={history.isLoading} />
          </>
        )}
      </div>
    </>
  );
}

function ChannelNote({ text, reasons }: { text: string | null; reasons: string[] }) {
  if (!text && !reasons.length) return null;
  return (
    <div className="mt-2 text-secondary text-pencil">
      {text && <p>{text}</p>}
      {reasons.length > 0 && <p className="mt-0.5">{reasons.join(' ')}</p>}
    </div>
  );
}

function History({ company, dispatches, loading }: { company: string; dispatches: DispatchOut[]; loading: boolean }) {
  const toast = useToast();
  if (loading) return null;
  if (!dispatches.length) return <p className="mt-4 text-center text-body text-pencil">Nothing sent to {company} yet.</p>;
  return (
    <section aria-labelledby="sent-h" className="mt-6">
      <SectionHeader as="h2" className="mb-2.5" count={dispatches.length}>
        <span id="sent-h">Sent</span>
      </SectionHeader>
      <Card padded={false} className="divide-y divide-hairline overflow-hidden">
        {dispatches.map((d) => {
          const Icon = CHANNEL_ICON[d.channel];
          const text = dispatchText(d);
          return (
            <div key={d.id} className="flex items-center gap-3 px-4 py-3">
              <Icon size={18} className="shrink-0 text-pencil" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-body text-graphite">
                  {plural(d.item_ids.length, 'item')}
                  {d.channel === 'outbox' ? ' in the Outbox' : d.destination ? ` to ${d.destination}` : ''}
                </p>
                <p className={cx('text-secondary', d.ok ? 'text-pencil' : 'text-safety')}>
                  {d.ok ? formatDateTime(d.sent_at) : `Didn’t go through. ${d.error ?? ''}`.trim()}
                </p>
              </div>
              {text && (
                <Button
                  variant="quiet"
                  icon={<Copy size={16} aria-hidden />}
                  aria-label={`Copy the message sent ${formatDateTime(d.sent_at)}`}
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
