// The live processing checklist (DESIGN.md "Record"): the pipeline's stages folded into the few steps a
// person cares about, each naming the model that does it. Pure, so it's tested against the replay.
import { STAGE_LABEL, type Stage, type StageProgressOut, type StageStatus } from '../../api/types';

export type RowStatus = 'done' | 'running' | 'pending' | 'failed';

export interface ChecklistRow {
  key: Stage;
  title: string;
  /** One plain line naming the model and the count ("Muse Voice Transcribe found 23 lines"). */
  detail: string | null;
  status: RowStatus;
  /** 0..1 while running, when the stage reports it. */
  progress: number | null;
  durationMs: number | null;
}

/** Each visible step, the stage that names it, and the quieter stages folded into it. */
const STEPS: { main: Stage; with: Stage[] }[] = [
  { main: 'transcribe', with: ['ingest'] },
  { main: 'track_hands', with: ['find_points'] },
  { main: 'fuse', with: ['classify_turns'] },
  { main: 'write_items', with: ['pick_evidence'] },
  { main: 'ground_defects', with: [] },
  { main: 'verify', with: [] },
  { main: 'resolve_closures', with: [] },
];

/** Saving the punch list folds into whichever step comes last. */
const TAIL: Stage = 'persist';

function groupStatus(statuses: StageStatus[]): RowStatus {
  if (statuses.includes('failed')) return 'failed';
  if (statuses.every((s) => s === 'done' || s === 'skipped')) return 'done';
  if (statuses.includes('running') || statuses.some((s) => s === 'done')) return 'running';
  return 'pending';
}

export function checklistRows(stages: StageProgressOut[]): ChecklistRow[] {
  const by = new Map(stages.map((s) => [s.stage, s]));
  const rows: ChecklistRow[] = [];
  for (const step of STEPS) {
    const main = by.get(step.main);
    const extras = step.with.map((s) => by.get(s)).filter(Boolean) as StageProgressOut[];
    if (!main) continue; // resolve_closures only exists on re-walks
    const all = [...extras, main];
    const status = groupStatus(all.map((s) => s.status));
    const failed = all.find((s) => s.status === 'failed');
    const runningExtra = extras.find((s) => s.status === 'running');
    const detail =
      (failed ? (failed.message ?? failed.detail) : null) ??
      (main.status === 'pending' && runningExtra ? (runningExtra.detail ?? runningExtra.label) : main.detail);
    const durations = all.map((s) => s.duration_ms).filter((d): d is number => d != null);
    rows.push({
      key: step.main,
      title: main.label || STAGE_LABEL[step.main],
      detail: detail ?? null,
      status,
      progress: main.status === 'running' ? main.progress : null,
      durationMs: status === 'done' && durations.length ? durations.reduce((a, b) => a + b, 0) : null,
    });
  }
  const tail = by.get(TAIL);
  const last = rows[rows.length - 1];
  if (tail && last && last.status === 'done' && tail.status !== 'done' && tail.status !== 'skipped') {
    last.status = tail.status === 'failed' ? 'failed' : tail.status === 'running' ? 'running' : last.status;
    if (tail.status === 'failed') last.detail = tail.detail ?? last.detail;
  }
  return rows;
}
