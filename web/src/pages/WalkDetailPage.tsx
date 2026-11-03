// Walk detail, the demo's hero screen. Placeholder for PLAN 5.5.
import { Link, useParams } from 'react-router';
import { PushedBar } from '../components';
import { useWalk } from '../api/hooks';
import { formatShortDate } from '../lib/format';
import { WALK_KIND_LABEL, plural } from '../lib/labels';
import { ComingSoon, ErrorState, Loading } from './common';

export default function WalkDetailPage() {
  const { id } = useParams();
  const walk = useWalk(id);
  const w = walk.data?.walk;
  const when = w?.recorded_at ?? w?.created_at;
  return (
    <>
      <PushedBar
        backTo="/walks"
        backLabel="Walks"
        title={w ? WALK_KIND_LABEL[w.kind] : undefined}
        meta={w && when ? `${formatShortDate(when)}, ${plural(w.counts.items, 'item')}` : undefined}
      />
      {walk.isLoading ? (
        <Loading label="Loading the walk" />
      ) : walk.isError ? (
        <ErrorState error={walk.error} onRetry={() => walk.refetch()} />
      ) : (
        <>
          <ComingSoon task="5.5" what="The video with the hand outline at each defect, item markers and the transcript." />
          <p className="px-4 text-center">
            <Link to={`/walks/${id}/processing`} className="inline-flex min-h-11 items-center text-body text-tape-ink underline">
              How this walk was processed
            </Link>
          </p>
        </>
      )}
    </>
  );
}
