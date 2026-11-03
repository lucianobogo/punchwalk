// Public read-only item page for trades (PLAN 6.1). Placeholder; no tab bar, no back link.
import { useParams } from 'react-router';
import { EmptyState, TapeTab } from '../components';
import { useShare } from '../api/hooks';
import { ComingSoon, ErrorState, Loading } from './common';

export default function SharePage() {
  const { token } = useParams();
  const share = useShare(token);
  if (share.isLoading) return <Loading label="Loading the item" />;
  if (share.isError) return <ErrorState error={share.error} />;
  if (!share.data) return <EmptyState title="This link has no item." />;
  return (
    <>
      <header className="flex items-center gap-3 px-4 pt-safe">
        <div className="flex min-h-16 items-center gap-3 pt-4">
          <TapeTab number={share.data.number} />
          <h1 className="text-title font-semibold font-expanded-title">{share.data.title}</h1>
        </div>
      </header>
      <p className="px-4 text-secondary text-pencil">{share.data.project_name}</p>
      <ComingSoon task="6.1" what="The evidence photo, the action needed and Play this moment." />
    </>
  );
}
