// Item detail. Placeholder for PLAN 5.3 (and the Play this moment sheet, 5.4).
import { useParams } from 'react-router';
import { PushedBar } from '../components';
import { useItem } from '../api/hooks';
import { ComingSoon, ErrorState, Loading } from './common';

export default function ItemDetailPage() {
  const { id } = useParams();
  const item = useItem(id);
  return (
    <>
      <PushedBar backTo="/" backLabel="Punch list" title={item.data?.title} />
      {item.isLoading ? (
        <Loading label="Loading the item" />
      ) : item.isError ? (
        <ErrorState error={item.error} onRetry={() => item.refetch()} />
      ) : (
        <ComingSoon task="5.3" what="Evidence photo, the action needed, the inspector’s quote and history." />
      )}
    </>
  );
}
