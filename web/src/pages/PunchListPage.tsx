// Punch list (home). Placeholder for PLAN 5.2; the title and counts already come from the data layer.
import { LargeTitleBar } from '../components';
import { useItems, useProject } from '../api/hooks';
import { useCurrentProject } from '../api/project';
import { ComingSoon, ErrorState, Loading } from './common';

export default function PunchListPage() {
  const { projectId } = useCurrentProject();
  const project = useProject(projectId);
  const items = useItems(projectId);

  return (
    <>
      <LargeTitleBar title={project.data?.name ?? 'Punch list'} />
      {items.isLoading || !projectId ? (
        <Loading label="Loading the punch list" />
      ) : items.isError ? (
        <ErrorState error={items.error} onRetry={() => items.refetch()} />
      ) : (
        <ComingSoon
          task="5.2"
          what={`The punch list. ${items.data?.counts.todo ?? 0} to do, ${items.data?.counts.signed_off ?? 0} signed off.`}
        />
      )}
    </>
  );
}
