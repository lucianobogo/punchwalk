// The clips of a walk, in order: thumbnail and length read from the File itself, move up/down, remove.
// Glasses walks arrive in parts, so order matters (it's the order the pipeline joins them).
import { ArrowDown, ArrowUp, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Card, IconButton } from '../../components';
import { formatClock } from '../../lib/format';

export interface Clip {
  id: string;
  file: File;
  duration: number | null;
  thumb: string | null;
}

/** Reads a clip's length and a frame for its thumbnail. Resolves with nulls when the browser can't decode it. */
export function readClipMeta(file: File): Promise<{ duration: number | null; thumb: string | null }> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const v = document.createElement('video');
    v.muted = true;
    v.playsInline = true;
    v.preload = 'auto';
    let done = false;
    const finish = (duration: number | null, thumb: string | null) => {
      if (done) return;
      done = true;
      // Detach before revoking, so the element doesn't fetch a revoked blob URL.
      v.removeAttribute('src');
      v.load();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      resolve({ duration, thumb });
    };
    const timer = setTimeout(() => finish(Number.isFinite(v.duration) ? v.duration : null, null), 6000);
    v.onerror = () => {
      clearTimeout(timer);
      finish(null, null);
    };
    v.onloadeddata = () => {
      // A frame from early in the clip (the very first frames are often black).
      v.currentTime = Math.min(5, (v.duration || 2) * 0.3);
    };
    const draw = () => {
      clearTimeout(timer);
      finish(Number.isFinite(v.duration) ? v.duration : null, grabFrame(v));
    };
    v.onseeked = () => {
      // Wait until the seeked frame is decoded before drawing it.
      const rv = v as HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => number };
      if (v.readyState >= 2) setTimeout(draw, 50);
      else if (rv.requestVideoFrameCallback) rv.requestVideoFrameCallback(draw);
      else v.addEventListener('canplay', draw, { once: true });
    };
    v.src = url;
  });
}

/** A small JPEG of the current frame, or null if it can't be read or is blank (all black). */
function grabFrame(v: HTMLVideoElement): string | null {
  try {
    const c = document.createElement('canvas');
    const scale = 160 / Math.max(v.videoWidth, v.videoHeight || 1);
    c.width = Math.max(1, Math.round(v.videoWidth * scale));
    c.height = Math.max(1, Math.round(v.videoHeight * scale));
    const ctx = c.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(v, 0, 0, c.width, c.height);
    const px = ctx.getImageData(0, 0, c.width, c.height).data;
    let lit = 0;
    for (let i = 0; i < px.length; i += 16) if (px[i] + px[i + 1] + px[i + 2] > 30) lit++;
    return lit > px.length / 16 / 20 ? c.toDataURL('image/jpeg', 0.7) : null;
  } catch {
    return null;
  }
}

let seq = 0;

/** Turns picked files into clips and fills in their metadata as it's read. */
export function useClips() {
  const [clips, setClips] = useState<Clip[]>([]);
  const add = (files: FileList | File[]) => {
    const next = [...files].map((file) => ({ id: `clip_${++seq}`, file, duration: null, thumb: null }));
    setClips((c) => [...c, ...next]);
    for (const clip of next) {
      readClipMeta(clip.file).then((meta) =>
        setClips((c) => c.map((x) => (x.id === clip.id ? { ...x, ...meta } : x))),
      );
    }
  };
  return { clips, setClips, add };
}

function sizeLabel(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 100 * 1024 * 1024 ? 1 : 0)} MB`;
}

export function ClipList({
  clips,
  onMove,
  onRemove,
  disabled,
}: {
  clips: Clip[];
  onMove: (index: number, delta: number) => void;
  onRemove: (index: number) => void;
  disabled?: boolean;
}) {
  return (
    <Card padded={false}>
      <ol aria-label="Clips in walk order" className="divide-y divide-hairline">
        {clips.map((c, i) => (
          <li key={c.id} className="flex items-center gap-2 py-2.5 pr-1 pl-3">
            <span className="w-4 shrink-0 text-center text-secondary tabular-nums text-pencil">{i + 1}</span>
            <ClipThumb clip={c} />
            <span className="ml-1 min-w-0 flex-1">
              <span className="block truncate text-row font-semibold text-graphite">{c.file.name}</span>
              <span className="block truncate text-secondary tabular-nums text-pencil">
                {c.duration != null ? formatClock(c.duration) : 'Reading length'}, {sizeLabel(c.file.size)}
              </span>
            </span>
            <IconButton label={`Move ${c.file.name} up`} disabled={disabled || i === 0} onClick={() => onMove(i, -1)} className="disabled:opacity-30">
              <ArrowUp size={18} aria-hidden />
            </IconButton>
            <IconButton
              label={`Move ${c.file.name} down`}
              disabled={disabled || i === clips.length - 1}
              onClick={() => onMove(i, 1)}
              className="disabled:opacity-30"
            >
              <ArrowDown size={18} aria-hidden />
            </IconButton>
            <IconButton label={`Remove ${c.file.name}`} disabled={disabled} onClick={() => onRemove(i)} className="disabled:opacity-30">
              <X size={18} aria-hidden />
            </IconButton>
          </li>
        ))}
      </ol>
    </Card>
  );
}

function ClipThumb({ clip }: { clip: Clip }) {
  const [ok, setOk] = useState(true);
  useEffect(() => setOk(true), [clip.thumb]);
  return clip.thumb && ok ? (
    <img src={clip.thumb} alt="" onError={() => setOk(false)} className="h-12 w-12 shrink-0 rounded-thumb bg-track object-cover" />
  ) : (
    <span className="block h-12 w-12 shrink-0 rounded-thumb bg-track" aria-hidden />
  );
}
