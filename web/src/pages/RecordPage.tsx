// Record (DESIGN.md "Record", 05-record-processing.png). One screen: choose the project, first walk or
// re-walk, add clips in order, upload with progress; then the live checklist naming each model.
// `?walk=<id>` shows the processing state of that walk (where an upload lands, and Walks links here).
import { Camera, Film } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ApiError } from '../api/api';
import { useProgress, useProjects, useRetryWalk, useUploadWalk, useWalk, useWalks } from '../api/hooks';
import { useCurrentProject } from '../api/project';
import type { JobProgressOut, WalkKind } from '../api/types';
import { Button, ButtonLink, Card, LargeTitleBar, ProgressBar, SegmentedControl, Skeleton, useToast } from '../components';
import { ClipList, useClips } from '../features/record/ClipList';
import { ItemsSoFar, ProcessingChecklist } from '../features/record/ProcessingChecklist';
import { clipsSummary, move, validateClips } from '../features/record/clips';
import { formatDuration, formatShortDate } from '../lib/format';
import { WALK_KIND_LABEL, plural } from '../lib/labels';
import { ErrorState, ProjectGate } from './common';

export default function RecordPage() {
  const [params] = useSearchParams();
  const walkId = params.get('walk');
  return walkId ? <Processing walkId={walkId} /> : <RecordForm />;
}

// ------------------------------------------------------------------ the form

const selectCls =
  'h-12 w-full rounded-button border border-hairline bg-sheet px-3 text-body text-graphite outline-none focus:border-tape-ink';

function RecordForm() {
  const navigate = useNavigate();
  const { projectId, setProjectId } = useCurrentProject();
  const projects = useProjects();
  const walks = useWalks(projectId);
  const upload = useUploadWalk();
  const { clips, setClips, add } = useClips();
  const [kind, setKind] = useState<WalkKind>('initial');
  const [rewalkOf, setRewalkOf] = useState<string>('');
  const [sent, setSent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [tried, setTried] = useState(false);
  const pickRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);

  const initialWalks = (walks.data?.walks ?? []).filter((w) => w.kind === 'initial' && w.status === 'ready');
  useEffect(() => {
    if (kind === 'rewalk' && !initialWalks.some((w) => w.id === rewalkOf)) setRewalkOf(initialWalks[0]?.id ?? '');
  }, [kind, initialWalks, rewalkOf]);

  const infos = clips.map((c) => ({ name: c.file.name, size: c.file.size, type: c.file.type, duration: c.duration }));
  const problems = validateClips(infos);
  const needsTarget = kind === 'rewalk' && !rewalkOf;
  const uploading = upload.isPending;

  async function start() {
    setTried(true);
    setError(null);
    if (problems.length || needsTarget || !projectId) return;
    setSent(0);
    try {
      const out = await upload.mutateAsync({
        projectId,
        req: { kind, rewalkOf: kind === 'rewalk' ? rewalkOf : null, clips: clips.map((c) => c.file) },
        onProgress: setSent,
      });
      navigate(`/record?walk=${out.walk_id}`, { replace: true });
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 429
          ? e.message || 'This demo takes 3 walks an hour from each connection. Try again a little later.'
          : e instanceof Error && e.message
            ? e.message
            : 'The upload stopped. Check the connection and try again.',
      );
    }
  }

  return (
    <>
      <LargeTitleBar title="Record a walk" />
      {!projectId ? (
        <div className="px-4 pt-4">
          <ProjectGate loadingLabel="Loading projects">{null}</ProjectGate>
        </div>
      ) : (
      <div className="flex flex-col gap-5 px-4 pt-4">
        <Field label="Project" htmlFor="rec-project">
          <select
            id="rec-project"
            className={selectCls}
            value={projectId ?? ''}
            onChange={(e) => setProjectId(e.target.value)}
            disabled={uploading}
          >
            {(projects.data?.projects ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="This walk is">
          <SegmentedControl<WalkKind>
            label="This walk is"
            value={kind}
            onChange={setKind}
            options={[
              { value: 'initial', label: WALK_KIND_LABEL.initial },
              { value: 'rewalk', label: WALK_KIND_LABEL.rewalk, disabled: !initialWalks.length },
            ]}
          />
          {kind === 'rewalk' && (
            <select
              aria-label="Re-walk of"
              className={`${selectCls} mt-2`}
              value={rewalkOf}
              onChange={(e) => setRewalkOf(e.target.value)}
              disabled={uploading}
            >
              {initialWalks.map((w) => (
                <option key={w.id} value={w.id}>
                  {`Checks the first walk of ${formatShortDate(w.recorded_at ?? w.created_at)}`}
                </option>
              ))}
            </select>
          )}
          {!initialWalks.length && (
            <p className="mt-1.5 text-secondary text-pencil">A re-walk checks a first walk. Record the first walk of this project before a re-walk.</p>
          )}
        </Field>

        <Field label="Clips, in the order you walked">
          {clips.length > 0 && (
            <ClipList
              clips={clips}
              disabled={uploading}
              onMove={(i, d) => setClips((c) => move(c, i, d))}
              onRemove={(i) => setClips((c) => c.filter((_, j) => j !== i))}
            />
          )}
          <div className="mt-2 flex gap-3">
            <Button variant="secondary" className="flex-1" icon={<Film size={18} aria-hidden />} onClick={() => pickRef.current?.click()} disabled={uploading}>
              {clips.length ? 'Add more clips' : 'Add clips'}
            </Button>
            <Button variant="secondary" className="flex-1" icon={<Camera size={18} aria-hidden />} onClick={() => cameraRef.current?.click()} disabled={uploading}>
              Record now
            </Button>
          </div>
          {/* Photos or Files (glasses clips arrive through the Meta AI app into Photos); "Record now" opens the camera. */}
          <input
            ref={pickRef}
            type="file"
            accept="video/*"
            multiple
            className="sr-only"
            tabIndex={-1}
            aria-label="Add clips from Photos or Files"
            onChange={(e) => {
              if (e.target.files?.length) add(e.target.files);
              e.target.value = '';
            }}
          />
          <input
            ref={cameraRef}
            type="file"
            accept="video/*"
            capture="environment"
            className="sr-only"
            tabIndex={-1}
            aria-label="Record a clip with the camera"
            onChange={(e) => {
              if (e.target.files?.length) add(e.target.files);
              e.target.value = '';
            }}
          />
          <p className="mt-2 text-secondary text-pencil">
            {clips.length ? clipsSummary(infos) : 'Glasses walks come in parts of up to 3 minutes. Add them all; Punchwalk joins them in order.'}
          </p>
        </Field>

        {(tried || clips.length > 0) && problems.length > 0 && clips.length > 0 && (
          <Card tone="flagged" role="alert">
            <ul className="flex flex-col gap-1 text-body text-graphite">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          </Card>
        )}
        {error && (
          <Card tone="flagged" role="alert">
            <p className="text-body text-graphite">{error}</p>
          </Card>
        )}

        <div>
          <Button
            size="lg"
            block
            onClick={start}
            disabled={!clips.length || problems.length > 0 || needsTarget || !projectId}
            busy={uploading}
            busyLabel={`Uploading, ${Math.round(sent * 100)}%`}
          >
            Upload walk
          </Button>
          {uploading && <ProgressBar value={sent} label="Upload" className="mt-3" />}
        </div>
      </div>
      )}
    </>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      {htmlFor ? (
        <label htmlFor={htmlFor} className="text-section font-medium text-pencil">
          {label}
        </label>
      ) : (
        <span className="text-section font-medium text-pencil">{label}</span>
      )}
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ processing

function Processing({ walkId }: { walkId: string }) {
  const progress = useProgress(walkId);
  const walk = useWalk(walkId);
  const p = progress.data;
  const w = walk.data?.walk;

  const subtitle = useMemo(() => {
    if (!w) return undefined;
    const clips = w.clips.length;
    const dur = w.duration_s != null ? `, ${formatDuration(w.duration_s * 1000)}` : '';
    return `${w.project_name}, ${WALK_KIND_LABEL[w.kind].toLowerCase()}. ${plural(clips, 'clip')}${dur}.`;
  }, [w]);

  const done = p?.status === 'done';
  const failed = p?.status === 'failed';
  const count = p?.items_so_far.length ?? 0;

  return (
    <>
      <LargeTitleBar
        title={done ? `${plural(count, 'item')} ready` : failed ? 'Processing stopped' : 'Processing walk'}
        subtitle={subtitle}
      />
      <div className="px-4 pt-4">
        {progress.isLoading ? (
          <div role="status" aria-label="Loading the walk’s progress">
            <Skeleton className="h-80 w-full rounded-card" />
          </div>
        ) : progress.isError || !p ? (
          <ErrorState error={progress.error} onRetry={() => progress.refetch()} />
        ) : (
          <ProcessingBody progress={p} walkProjectId={w?.project_id} walkKind={w?.kind} />
        )}
      </div>
    </>
  );
}

export function ProcessingBody({
  progress: p,
  walkProjectId,
  walkKind,
}: {
  progress: JobProgressOut;
  walkProjectId?: string;
  walkKind?: WalkKind;
}) {
  const { setProjectId } = useCurrentProject();
  const retry = useRetryWalk();
  const toast = useToast();
  const done = p.status === 'done';
  const failed = p.status === 'failed';
  const qc = useQueryClient();
  const written = p.items_so_far.length;

  // Items appear on the punch list as they're written: refresh the lists when the count or status moves.
  useEffect(() => {
    for (const key of ['items', 'project', 'projects', 'walks', 'walk', 'trades']) qc.invalidateQueries({ queryKey: [key] });
  }, [qc, written, p.status]);

  return (
    <>
      {failed && (
        <Card tone="flagged" role="alert" className="mb-4">
          <p className="text-body text-graphite">{p.error ?? 'Processing stopped before the punch list was written.'}</p>
          <Button
            className="mt-3"
            busy={retry.isPending}
            busyLabel="Starting again…"
            onClick={async () => {
              try {
                await retry.mutateAsync(p.walk_id);
                toast.show('Processing the walk again');
              } catch (e) {
                toast.show(e instanceof Error ? e.message : 'That didn’t start. Try again in a moment.', { tone: 'error' });
              }
            }}
          >
            Try again
          </Button>
        </Card>
      )}
      {done && (
        <div className="mb-4 flex flex-col gap-2">
          {walkKind === 'rewalk' ? (
            <ButtonLink to={`/walks/${p.walk_id}/rewalk`} size="lg" block onClick={() => walkProjectId && setProjectId(walkProjectId)}>
              See what was signed off
            </ButtonLink>
          ) : (
            <ButtonLink to="/" size="lg" block onClick={() => walkProjectId && setProjectId(walkProjectId)}>
              Open punch list
            </ButtonLink>
          )}
          <ButtonLink to={`/walks/${p.walk_id}/processing`} variant="secondary" size="lg" block>
            See how it was processed
          </ButtonLink>
        </div>
      )}
      <ProcessingChecklist progress={p} />
      <ItemsSoFar progress={p} />
      {!done && !failed && (
        <p className="mt-5 text-body text-pencil">
          {p.status === 'queued' ? 'Waiting for its turn. It starts on its own as soon as the server is free. ' : ''}
          You can leave this screen. Items appear on the punch list as they’re written.
        </p>
      )}
    </>
  );
}
