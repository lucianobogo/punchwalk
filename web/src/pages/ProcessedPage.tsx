// How this walk was processed (DESIGN.md, 08-how-processed.png): "Done in 1 min 52 s for about $0.44,
// using three Meta models", each step with its model chip, detail line and duration, every model call,
// and the eval numbers from eval/report.md (mock numbers are labeled as samples; none -> hidden).
import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { DATA_MODE } from '../api/client';
import { useProcessing, useWalk } from '../api/hooks';
import type { EvalSummaryOut, ModelCallOut, ProcessingStepOut, ProcessingSummaryOut } from '../api/types';
import { Button, Card, Pill, PushedBar, SectionHeader, Skeleton } from '../components';
import { checkerName } from '../features/item/actions';
import { formatCost, formatDuration } from '../lib/format';
import { STAGE_LABEL, WALK_KIND_LABEL, plural } from '../lib/labels';
import { cx } from '../lib/cx';
import { ErrorState } from './common';

const NUMBER_WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six'];

/** "Done in 1 min 52 s for about $0.44, using three Meta models." */
export function summaryLine(p: Pick<ProcessingSummaryOut, 'total_duration_ms' | 'total_cost_usd' | 'models_used'>): string {
  const n = p.models_used.length;
  const models = `${NUMBER_WORDS[n] ?? n} Meta ${n === 1 ? 'model' : 'models'}`;
  return `Done in ${formatDuration(p.total_duration_ms)} for about ${formatCost(p.total_cost_usd)}, using ${models}.`;
}

/** Eval numbers are shown only when they're real, or labeled as samples while browsing mock data. */
export function evalVisibility(ev: EvalSummaryOut | null, dataMode = DATA_MODE): 'hidden' | 'sample' | 'real' {
  if (!ev || !ev.metrics.length) return 'hidden';
  if (ev.source === 'mock') return dataMode === 'mock' ? 'sample' : 'hidden';
  return 'real';
}

export default function ProcessedPage() {
  const { id } = useParams();
  const walk = useWalk(id);
  const p = useProcessing(id);
  const back = walk.data ? WALK_KIND_LABEL[walk.data.walk.kind] : 'Walk';

  return (
    <>
      <PushedBar
        backTo={`/walks/${id}`}
        backLabel={back}
        title="How this walk was processed"
        subtitle={p.data ? summaryLine(p.data) : undefined}
      />
      <div className="px-4 pt-4">
        {p.isLoading ? (
          <div role="status" aria-label="Loading the model-call log">
            <Skeleton className="h-96 w-full rounded-card" />
          </div>
        ) : p.isError || !p.data ? (
          <ErrorState error={p.error} onRetry={() => p.refetch()} />
        ) : (
          <Body p={p.data} />
        )}
      </div>
    </>
  );
}

function Body({ p }: { p: ProcessingSummaryOut }) {
  const ev = evalVisibility(p.eval);
  return (
    <>
      <Card padded={false} className="py-2">
        <ol aria-label="Steps">
          {p.steps.map((s) => (
            <Step key={s.stage} s={s} />
          ))}
        </ol>
      </Card>

      {ev !== 'hidden' && p.eval && (
        <section aria-labelledby="eval-h" className="mt-6">
          <SectionHeader as="h2" className="mb-2.5">
            <span id="eval-h">{ev === 'sample' ? 'On our labeled test walks (sample numbers)' : 'On our labeled test walks'}</span>
          </SectionHeader>
          <Card padded={false} className="divide-y divide-hairline px-4">
            {p.eval.metrics.map((m) => (
              <div key={m.key} className="flex min-h-13 items-center gap-4 py-3">
                <span className="flex-1 text-card text-graphite">{m.label}</span>
                <span className="text-card font-semibold tabular-nums text-graphite">
                  {m.value} of {m.total}
                </span>
              </div>
            ))}
          </Card>
          {ev === 'sample' && (
            <p className="mt-2 text-secondary text-pencil">These are sample numbers from the mock data. The real ones come from eval/report.md.</p>
          )}
        </section>
      )}

      <Calls calls={p.calls} />

      <p className="mt-6 text-center">
        <Link to={`/walks/${p.walk_id}`} className="inline-flex min-h-11 items-center text-body text-tape-ink underline">
          Watch the walk
        </Link>
      </p>
    </>
  );
}

function Step({ s }: { s: ProcessingStepOut }) {
  return (
    <li className="flex gap-4 px-4 py-3.5">
      <span className="mt-[7px] size-3 shrink-0 rounded-full bg-graphite" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-3">
          <h3 className="min-w-0 flex-1 text-card font-semibold text-graphite">{s.title}</h3>
          <span className="shrink-0 text-body tabular-nums text-pencil">{formatDuration(s.duration_ms)}</span>
        </div>
        {(s.model_label || s.fallback_calls > 0) && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {s.model_label && <Pill>{s.model_label}</Pill>}
            {s.fallback_calls > 0 && <Pill tone="hold">{`Muse Spark 1.3 (fallback), ${plural(s.fallback_calls, 'call')}`}</Pill>}
          </div>
        )}
        <p className="mt-1.5 text-body text-pencil">{s.detail}</p>
        {s.calls > 0 && (
          <p className="mt-0.5 text-secondary tabular-nums text-pencil">
            {plural(s.calls, 'call')}, about {formatCost(s.cost_usd)}
          </p>
        )}
      </div>
    </li>
  );
}

function callModel(c: ModelCallOut): string {
  if (c.provider === 'meta-fallback') return checkerName('meta-fallback');
  return c.model_label;
}

function Calls({ calls }: { calls: ModelCallOut[] }) {
  const [open, setOpen] = useState(false);
  if (!calls.length) return null;
  return (
    <section aria-labelledby="calls-h" className="mt-6">
      <div className="mb-2.5 flex items-center">
        <SectionHeader as="h2" className="flex-1" count={calls.length}>
          <span id="calls-h">Every model call</span>
        </SectionHeader>
        <Button variant="quiet" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls="calls-list">
          {open ? 'Hide' : 'Show all'}
        </Button>
      </div>
      {open && (
        <Card padded={false} id="calls-list" className="divide-y divide-hairline overflow-hidden">
          {calls.map((c) => (
            <div key={c.id} className={cx('flex items-start gap-3 px-4 py-3', c.provider === 'meta-fallback' && 'bg-hold-card')}>
              <div className="min-w-0 flex-1">
                <p className="text-body font-medium text-graphite">{callModel(c)}</p>
                <p className="text-secondary text-pencil">
                  {c.stage ? STAGE_LABEL[c.stage] : 'Other'}
                  {c.frames ? `, ${c.frames} frames` : ''}
                  {c.tokens_in != null ? `, ${c.tokens_in + (c.tokens_out ?? 0)} tokens` : ''}
                  {c.cached ? ', from cache' : ''}
                </p>
                {!c.ok && c.error && <p className="text-secondary text-safety">{c.error}</p>}
              </div>
              <div className="shrink-0 text-right text-secondary tabular-nums text-pencil">
                <p>{formatDuration(c.latency_ms)}</p>
                <p>{formatCost(c.est_cost_usd)}</p>
              </div>
            </div>
          ))}
        </Card>
      )}
    </section>
  );
}
