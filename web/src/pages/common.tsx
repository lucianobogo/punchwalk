// Shared bits for pages: the "coming in 5.x" placeholder and a readable error state.
import { Button, EmptyState, SkeletonRows } from '../components';
import { ApiError } from '../api/api';

export function ComingSoon({ task, what }: { task: string; what: string }) {
  return <EmptyState title={`Coming in ${task}`} body={what} className="py-16" />;
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const notFound = error instanceof ApiError && error.status === 404;
  const message =
    error instanceof Error && error.message ? error.message : 'Something went wrong loading this screen.';
  return (
    <EmptyState
      title={notFound ? 'This page doesn’t exist anymore.' : 'This didn’t load.'}
      body={notFound ? 'It may have been dismissed or moved. Go back and pick it again.' : message}
      action={
        onRetry && !notFound ? (
          <Button variant="secondary" onClick={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
    />
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="px-4 pt-4">
      <SkeletonRows label={label} count={4} />
    </div>
  );
}
