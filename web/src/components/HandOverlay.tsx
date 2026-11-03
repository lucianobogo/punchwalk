// Draws the overlay over a <video> with object-fit: cover: the inspector's hand outlined in Tape blue,
// and the item's number tab at the fingertip while pointing (DESIGN.md "Signature component").
// Nothing is drawn between points. Coordinates are in the overlay's width x height (not the video's
// pixel size; mock videos are downscaled).
import { useRef } from 'react';
import type { OverlayFrameOut, OverlayOut } from '../api/types';
import { coverMap, useElementSize } from '../lib/video';
import { TapeTab } from './TapeTab';

/** The overlay frame for time t, or null if no frame is within one sample (1/fps). */
export function frameAt(overlay: OverlayOut | undefined, t: number): OverlayFrameOut | null {
  const frames = overlay?.frames;
  if (!frames?.length) return null;
  let lo = 0;
  let hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].t < t) lo = mid + 1;
    else hi = mid;
  }
  const candidates = [frames[lo], frames[lo - 1]].filter(Boolean) as OverlayFrameOut[];
  const best = candidates.reduce((a, b) => (Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a));
  return Math.abs(best.t - t) <= 1 / (overlay!.fps || 10) ? best : null;
}

export function HandOverlay({
  overlay,
  time,
  onlyNumber,
}: {
  overlay: OverlayOut | undefined;
  time: number;
  /** Only draw this item's hand (Play this moment); undefined = all. */
  onlyNumber?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const size = useElementSize(box);
  const frame = frameAt(overlay, time);
  const hands = (frame?.hands ?? []).filter((h) => onlyNumber == null || h.number == null || h.number === onlyNumber);
  const W = overlay?.width ?? 1;
  const H = overlay?.height ?? 1;
  const map = coverMap(size, W, H);

  return (
    <div ref={box} className="pointer-events-none absolute inset-0" aria-hidden>
      {hands.length > 0 && (
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice" className="absolute inset-0 size-full">
          {hands.map((h) => (
            <g key={h.point_id}>
              <path d={h.svg} fill="none" stroke="var(--color-white)" strokeWidth={6} vectorEffect="non-scaling-stroke" strokeLinejoin="round" opacity={0.85} />
              <path
                d={h.svg}
                fill="var(--color-tape)"
                fillOpacity={0.12}
                stroke="var(--color-tape)"
                strokeWidth={3}
                vectorEffect="non-scaling-stroke"
                strokeLinejoin="round"
              />
            </g>
          ))}
        </svg>
      )}
      {hands.map((h) =>
        h.fingertip && h.number != null ? (
          <span
            key={`tab-${h.point_id}`}
            className="absolute"
            style={{ left: map.x(h.fingertip.x), top: map.y(h.fingertip.y), transform: 'translate(-50%, calc(-100% - 10px))' }}
          >
            <TapeTab number={h.number} pressOn decorative />
          </span>
        ) : null,
      )}
    </div>
  );
}
