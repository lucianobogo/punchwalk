// Re-walk summary (PLAN 7.4). Placeholder.
import { useParams } from 'react-router';
import { PushedBar } from '../components';
import { useRewalkSummary } from '../api/hooks';
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
