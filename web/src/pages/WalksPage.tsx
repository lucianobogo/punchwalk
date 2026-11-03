// Walks list. Placeholder for PLAN 5.5.
import { LargeTitleBar } from '../components';
import { useWalks } from '../api/hooks';
import { plural } from '../lib/labels';
import { ComingSoon, ErrorState, Loading } from './common';

export default function WalksPage() {
  const walks = useWalks();
  return (
    <>
      <LargeTitleBar title="Walks" />
      {walks.isLoading ? (
        <Loading label="Loading walks" />
      ) : walks.isError ? (
        <ErrorState error={walks.error} onRetry={() => walks.refetch()} />
      ) : (
        <ComingSoon task="5.5" what={`Every walk with its status. ${plural(walks.data?.walks.length ?? 0, 'walk')} so far.`} />
      )}
    </>
  );
}
