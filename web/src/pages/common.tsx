// Shared bits for pages: the "coming in 5.x" placeholder and a readable error state.
import { Button, Card, EmptyState, SkeletonRows } from '../components';
import { useCurrentProject } from '../api/project';
import { ApiError } from '../api/api';

export function ComingSoon({ task, what }: { task: string; what: string }) {
  return <EmptyState title={`Coming in ${task}`} body={what} className="py-16" />;
}

export function ErrorState({
  error,
  onRetry,
  notFound: notFoundTitle = 'This page isn’t here anymore.',
}: {
  error: unknown;
  onRetry?: () => void;
  /** What a 404 means on this screen ("This item isn’t here anymore."). */
  notFound?: string;
}) {
  const notFound = error instanceof ApiError && error.status === 404;
  const message =
    error instanceof Error && error.message ? error.message : 'Something went wrong loading this screen. Try again in a moment.';
  return (
    <div className="pt-2" role="alert">
      <Card className="text-center">
        <EmptyState
          className="py-6"
          title={notFound ? notFoundTitle : 'This didn’t load.'}
          body={notFound ? 'It may have been dismissed or moved. Go back and pick it again.' : message}
          action={
            onRetry && !notFound ? (
              <Button variant="secondary" onClick={onRetry}>
                Try again
              </Button>
            ) : undefined
          }
        />
      </Card>
    </div>
  );
}

/** For screens that need the current project: loading, failed, or none on this server yet. */
export function ProjectGate({ children, loadingLabel }: { children: React.ReactNode; loadingLabel: string }) {
  const { projectId, status, error, retry } = useCurrentProject();
  if (status === 'error') return <ErrorState error={error} onRetry={retry} />;
  if (status === 'empty') {
    return (
      <EmptyState
        title="There’s no project here yet."
        body="Once the demo project is seeded, its punch list shows up here."
      />
    );
  }
  if (!projectId) return <SkeletonRows label={loadingLabel} count={4} />;
  return <>{children}</>;
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="px-4 pt-4">
      <SkeletonRows label={label} count={4} />
    </div>
  );
}
