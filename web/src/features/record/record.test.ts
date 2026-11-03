import { describe, expect, it } from 'vitest';
import { diskMock } from '../../test/fixtures';
import type { JobProgressOut } from '../../api/types';
import { FAILED_HERE, checklistRows, readableFailure } from './checklist';
import { MAX_WALK_BYTES, clipsSummary, move, validateClips, type ClipInfo } from './clips';

const clip = (over: Partial<ClipInfo> = {}): ClipInfo => ({ name: 'walk1_a.mp4', size: 50 * 1024 * 1024, type: 'video/mp4', duration: 150, ...over });

describe('clip validation', () => {
  it('accepts a normal two-part glasses walk', () => {
    expect(validateClips([clip(), clip({ name: 'walk1_b.mp4', duration: 120 })])).toEqual([]);
  });

  it('asks for at least one clip', () => {
    expect(validateClips([])).toEqual(['Add at least one clip of the walk.']);
  });

  it('blocks a walk over 10 minutes, in plain words', () => {
    expect(validateClips([clip({ duration: 601 })])).toEqual([
      'This walk is longer than 10 minutes. Record it as two separate walks.',
    ]);
    const parts = validateClips([clip({ duration: 400 }), clip({ name: 'b.mp4', duration: 300 })]);
    expect(parts[0]).toMatch(/^These clips add up to 11 min 40 s\. A walk can be up to 10 minutes/);
    expect(validateClips([clip({ duration: 600 })])).toEqual([]);
  });

  it('blocks more than 400 MB', () => {
    const errs = validateClips([clip({ size: MAX_WALK_BYTES / 2 + 1 }), clip({ name: 'b.mp4', size: MAX_WALK_BYTES / 2 })]);
    expect(errs[0]).toMatch(/^These clips add up to 400 MB|^These clips add up to 401 MB/);
    expect(validateClips([clip({ size: MAX_WALK_BYTES })])).toEqual([]);
  });

  it("says when a file isn't a video, and lets unknown lengths through (the server checks)", () => {
    expect(validateClips([clip({ name: 'notes.txt', type: 'text/plain' })])[0]).toMatch(/notes\.txt isn’t a video/);
    expect(validateClips([clip({ name: 'IMG_0001.MOV', type: '' })])).toEqual([]);
    expect(validateClips([clip({ duration: null })])).toEqual([]);
  });

  it('summarizes and reorders', () => {
    expect(clipsSummary([clip({ duration: 82.4 }), clip({ duration: 71.1 })])).toBe('2 clips, 2 min 34 s.');
    expect(clipsSummary([clip({ duration: null })])).toBe('1 clip.');
    expect(move(['a', 'b', 'c'], 2, -1)).toEqual(['a', 'c', 'b']);
    expect(move(['a', 'b'], 0, -1)).toEqual(['a', 'b']);
  });
});

async function snapshot(n: number): Promise<JobProgressOut> {
  const { api, control } = diskMock();
  control.setProgressStep(n);
  return api.progress('wlk_3a_first');
}

describe('processing checklist', () => {
  it('folds the pipeline into the steps DESIGN.md names, each with its model', async () => {
    const rows = checklistRows((await snapshot(1)).stages);
    expect(rows.map((r) => r.title)).toEqual([
      'Listening to the walk',
      'Following your hand (SAM 3.1)',
      'Matching what was said to each point',
      'Writing items',
      'Outlining each defect',
      'Checking each item against its photo',
    ]);
    expect(rows.every((r) => r.status === 'pending')).toBe(true);
    expect(rows[0].detail).toBe('Muse Voice Transcribe, waiting');
  });

  it('shows the running step with its count and progress, done steps with timings', async () => {
    const rows = checklistRows((await snapshot(5)).stages);
    const byTitle = Object.fromEntries(rows.map((r) => [r.title, r]));
    expect(byTitle['Listening to the walk']).toMatchObject({ status: 'done', detail: 'Muse Voice Transcribe found 10 lines' });
    expect(byTitle['Listening to the walk'].durationMs).toBe(7400 + 5900); // ingest folds into it
    expect(byTitle['Following your hand (SAM 3.1)']).toMatchObject({ status: 'done', detail: '4 points found' });
    expect(byTitle['Writing items']).toMatchObject({ status: 'running', progress: 0.5 });
    expect(byTitle['Writing items'].detail).toMatch(/Muse Spark Open on Baseten, 2 of 4 written/);
    expect(byTitle['Checking each item against its photo'].status).toBe('pending');
  });

  it('marks a step running while a quieter stage folded into it runs', async () => {
    const rows = checklistRows((await snapshot(2)).stages);
    expect(rows[0]).toMatchObject({ status: 'running', detail: 'Joining clips and preparing the video' });
  });

  it('ends with every step done', async () => {
    expect(checklistRows((await snapshot(8)).stages).every((r) => r.status === 'done')).toBe(true);
  });

  it('shows a failure on its step, in words', () => {
    const rows = checklistRows([
      { stage: 'transcribe', label: 'Listening to the walk', status: 'done', started_at: null, finished_at: null, duration_ms: 1000, counts: {}, progress: null, detail: 'ok', model_label: null, message: null },
      { stage: 'track_hands', label: 'Following your hand (SAM 3.1)', status: 'failed', started_at: null, finished_at: null, duration_ms: null, counts: {}, progress: null, detail: 'SAM 3.1, 71 of 780 frames', model_label: 'SAM 3.1', message: 'SAM 3.1 timed out twice.' },
    ]);
    expect(rows[1]).toMatchObject({ status: 'failed', detail: 'SAM 3.1 timed out twice.' });
  });

  it('keeps internal error text out of the checklist', () => {
    const raw =
      'transcribe failed: CacheMiss: cache miss in readonly mode for ASR audio.wav @ 0 ms (672078 bytes) (key c8f0511a0be9a860f842b5ffa6c992430f441a575) Record it with a live run (`make probe`).';
    expect(readableFailure(raw)).toBeNull();
    expect(readableFailure('Transcription failed: a recorded model answer is missing (offline mode).')).toMatch(/^Transcription failed/);
    const rows = checklistRows([
      { stage: 'transcribe', label: 'Listening to the walk', status: 'failed', started_at: null, finished_at: null, duration_ms: null, counts: {}, progress: null, detail: raw, model_label: null, message: raw },
    ]);
    expect(rows[0].detail).toBe(FAILED_HERE);
  });
});
