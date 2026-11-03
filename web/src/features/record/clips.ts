// Clip checks before upload. Limits from CLAUDE.md (Muse Voice Transcribe: <= 10 minutes of audio)
// and the API's upload guard (400 MB). Errors say what happened and what to do, without apologizing.

export const MAX_WALK_S = 10 * 60;
export const MAX_WALK_BYTES = 400 * 1024 * 1024;

export interface ClipInfo {
  name: string;
  size: number;
  type: string;
  /** Seconds, from the file's metadata; null until read or when the browser can't read it. */
  duration: number | null;
}

const VIDEO_EXT = /\.(mp4|mov|m4v|qt)$/i;

export function isVideo(c: Pick<ClipInfo, 'name' | 'type'>): boolean {
  return c.type.startsWith('video/') || VIDEO_EXT.test(c.name);
}

function minutes(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return sec ? `${m} min ${sec} s` : `${m} min`;
}

function megabytes(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

/** Plain-words problems that block the upload; empty when it's good to go. */
export function validateClips(clips: ClipInfo[]): string[] {
  const errors: string[] = [];
  if (!clips.length) return ['Add at least one clip of the walk.'];
  for (const c of clips) {
    if (!isVideo(c)) errors.push(`${c.name} isn’t a video. Add MP4 or MOV clips from the glasses or the phone.`);
    else if (c.size === 0) errors.push(`${c.name} is empty. Export it again from Photos and add it here.`);
  }
  const total = clips.reduce((n, c) => n + (c.duration ?? 0), 0);
  if (total > MAX_WALK_S) {
    errors.push(
      clips.length > 1
        ? `These clips add up to ${minutes(total)}. A walk can be up to 10 minutes, so upload the rest as a second walk.`
        : 'This walk is longer than 10 minutes. Split it into parts and upload them together.',
    );
  }
  const bytes = clips.reduce((n, c) => n + c.size, 0);
  if (bytes > MAX_WALK_BYTES) {
    errors.push(`These clips add up to ${megabytes(bytes)}. A walk can be up to 400 MB, so export them at 1080p or trim them.`);
  }
  return errors;
}

/** "2 clips, 2 min 33 s." (duration left out until every clip's length is known) */
export function clipsSummary(clips: ClipInfo[]): string {
  const n = `${clips.length} ${clips.length === 1 ? 'clip' : 'clips'}`;
  if (!clips.length || clips.some((c) => c.duration == null)) return `${n}.`;
  return `${n}, ${minutes(clips.reduce((t, c) => t + (c.duration ?? 0), 0))}.`;
}

/** Moves item i by delta (-1 up, +1 down), keeping everything else in order. */
export function move<T>(list: T[], i: number, delta: number): T[] {
  const j = i + delta;
  if (j < 0 || j >= list.length) return list;
  const out = [...list];
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}
