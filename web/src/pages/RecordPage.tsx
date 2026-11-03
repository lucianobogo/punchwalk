// Record: choose project, first walk or re-walk, add clips, upload, then the live checklist.
// Placeholder for PLAN 5.6. `?walk=<id>` shows the processing state of that walk.
import { useSearchParams } from 'react-router';
import { LargeTitleBar } from '../components';
import { useProgress } from '../api/hooks';
import { ComingSoon } from './common';

export default function RecordPage() {
  const [params] = useSearchParams();
  const walkId = params.get('walk') ?? undefined;
  const progress = useProgress(walkId);
  const processing = Boolean(walkId);
  return (
    <>
      <LargeTitleBar title={processing ? 'Processing walk' : 'Record a walk'} />
      <ComingSoon
        task="5.6"
        what={
          processing
            ? `The live checklist. ${Math.round((progress.data?.progress ?? 0) * 100)}% done.`
            : 'Choose the project, add clips in order and upload the walk.'
        }
      />
    </>
  );
}
