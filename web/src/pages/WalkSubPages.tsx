// Re-walk summary (PLAN 7.4) and How this walk was processed (PLAN 5.7). Placeholders.
import { useParams } from 'react-router';
import { PushedBar } from '../components';
import { useProcessing, useRewalkSummary, useWalk } from '../api/hooks';
import { formatCost, formatDuration } from '../lib/format';
import { WALK_KIND_LABEL } from '../lib/labels';
import { ComingSoon, ErrorState, Loading } from './common';

export function RewalkPage() {
  const { id } = useParams();
  const summary = useRewalkSummary(id);
  return (
    <>
      <PushedBar backTo="/walks" backLabel="Walks" title="Re-walk" subtitle={summary.data?.headline} />
      {summary.isLoading ? (
        <Loading label="Loading the re-walk summary" />
      ) : summary.isError ? (
        <ErrorState error={summary.error} onRetry={() => summary.refetch()} />
      ) : (
        <ComingSoon task="7.4" what="What was signed off, what is still open, and what needs a look." />
      )}
    </>
  );
}

export function ProcessedPage() {
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
        subtitle={
          p.data
            ? `Done in ${formatDuration(p.data.total_duration_ms)} for about ${formatCost(p.data.total_cost_usd)}.`
            : undefined
        }
      />
      {p.isLoading ? (
        <Loading label="Loading the model-call log" />
      ) : p.isError ? (
        <ErrorState error={p.error} onRetry={() => p.refetch()} />
      ) : (
        <ComingSoon task="5.7" what="Each step with its Meta model, what it found and how long it took." />
      )}
    </>
  );
}
