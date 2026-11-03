// The evidence photo (DESIGN.md "Item detail"): inset 16 px, radius 14, tape tab on the corner,
// pinch-to-zoom, and an "Outlines on" toggle. With outlines on, the defect is outlined in red
// (30% fill, white halo) and a small blue dot marks where the finger pointed; off shows the plain frame.
//
// Drawn as one SVG: the raw frame as an <image>, the outlines on top, and the viewBox set to the
// evidence crop (4:3, centered on the pointed spot and the defect), so zoom keeps them aligned.
import { Eye, EyeOff } from 'lucide-react';
import { useRef, useState, type PointerEvent as RPointerEvent } from 'react';
import type { BoxOut, EvidenceOut } from '../../api/types';
import { TapeTab, type TapeTabBadge } from '../../components';
import { useElementSize } from '../../lib/video';
import { cx } from '../../lib/cx';

const ASPECT = 4 / 3;

/** The 4:3 region to show: the evidence crop, else a box around the pointer/defect, else the frame. */
export function viewBoxFor(ev: EvidenceOut): BoxOut {
  const W = ev.width;
  const H = ev.height;
  let box = ev.crop_box;
  if (!box) {
    const focus = ev.defect_box ?? (ev.pointer ? { x1: ev.pointer.x, y1: ev.pointer.y, x2: ev.pointer.x, y2: ev.pointer.y } : null);
    if (!focus) {
      const h = Math.min(H, W / ASPECT);
      return { x1: 0, y1: (H - h) / 2, x2: W, y2: (H + h) / 2 };
    }
    const cx = (focus.x1 + focus.x2) / 2;
    const cy = (focus.y1 + focus.y2) / 2;
    const w = Math.min(W, Math.max(W * 0.55, (focus.x2 - focus.x1) * 3));
    box = { x1: cx - w / 2, y1: cy - w / ASPECT / 2, x2: cx + w / 2, y2: cy + w / ASPECT / 2 };
  }
  // Clamp into the frame.
  const w = box.x2 - box.x1;
  const h = box.y2 - box.y1;
  const x1 = Math.min(Math.max(0, box.x1), Math.max(0, W - w));
  const y1 = Math.min(Math.max(0, box.y1), Math.max(0, H - h));
  return { x1, y1, x2: x1 + w, y2: y1 + h };
}

interface Zoom {
  scale: number;
  x: number;
  y: number;
}

const MAX_ZOOM = 4;

/** Two-finger pinch and one-finger pan once zoomed; double-tap toggles 2.5x. No library. */
function usePinchZoom() {
  const [z, setZ] = useState<Zoom>({ scale: 1, x: 0, y: 0 });
  const pts = useRef(new Map<number, { x: number; y: number }>());
  const start = useRef<{ dist: number; z: Zoom; mid: { x: number; y: number } } | null>(null);
  const lastTap = useRef(0);

  const clampPan = (next: Zoom, el: HTMLElement): Zoom => {
    const maxX = ((next.scale - 1) * el.clientWidth) / 2;
    const maxY = ((next.scale - 1) * el.clientHeight) / 2;
    return { scale: next.scale, x: Math.max(-maxX, Math.min(maxX, next.x)), y: Math.max(-maxY, Math.min(maxY, next.y)) };
  };

  const onPointerDown = (e: RPointerEvent<HTMLElement>) => {
    pts.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.current.size === 2) {
      const [a, b] = [...pts.current.values()];
      start.current = { dist: Math.hypot(a.x - b.x, a.y - b.y), z, mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
      e.currentTarget.setPointerCapture(e.pointerId);
    } else if (pts.current.size === 1) {
      const now = Date.now();
      if (now - lastTap.current < 300) {
        setZ((cur) => (cur.scale > 1 ? { scale: 1, x: 0, y: 0 } : { scale: 2.5, x: 0, y: 0 }));
        lastTap.current = 0;
      } else lastTap.current = now;
      start.current = { dist: 0, z, mid: { x: e.clientX, y: e.clientY } };
      if (z.scale > 1) e.currentTarget.setPointerCapture(e.pointerId);
    }
  };

  const onPointerMove = (e: RPointerEvent<HTMLElement>) => {
    if (!pts.current.has(e.pointerId) || !start.current) return;
    pts.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const el = e.currentTarget;
    if (pts.current.size >= 2 && start.current.dist > 0) {
      const [a, b] = [...pts.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const scale = Math.min(MAX_ZOOM, Math.max(1, (start.current.z.scale * dist) / start.current.dist));
      setZ(clampPan({ scale, x: start.current.z.x + (mid.x - start.current.mid.x), y: start.current.z.y + (mid.y - start.current.mid.y) }, el));
    } else if (pts.current.size === 1 && start.current.z.scale > 1) {
      setZ(clampPan({ ...start.current.z, x: start.current.z.x + (e.clientX - start.current.mid.x), y: start.current.z.y + (e.clientY - start.current.mid.y) }, el));
    }
  };

  const onPointerUp = (e: RPointerEvent<HTMLElement>) => {
    pts.current.delete(e.pointerId);
    start.current = pts.current.size ? { dist: 0, z, mid: [...pts.current.values()][0] } : null;
    if (z.scale < 1.05) setZ({ scale: 1, x: 0, y: 0 });
  };

  return {
    zoom: z,
    reset: () => setZ({ scale: 1, x: 0, y: 0 }),
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp },
  };
}

export function EvidencePhoto({
  evidence,
  number,
  badge,
  alt,
}: {
  evidence: EvidenceOut;
  number: number;
  badge?: TapeTabBadge;
  alt: string;
}) {
  const [outlines, setOutlines] = useState(true);
  const frameRef = useRef<HTMLDivElement>(null);
  const size = useElementSize(frameRef);
  const { zoom, handlers } = usePinchZoom();
  const vb = viewBoxFor(evidence);
  const vbW = vb.x2 - vb.x1;
  // Screen-pixel sizes in frame units (the viewBox is scaled to the rendered width).
  const px = size.width > 0 ? vbW / size.width / zoom.scale : 1;
  const hasMarks = Boolean(evidence.defect_svg || evidence.pointer);

  return (
    <div className="relative mx-4">
      <div
        ref={frameRef}
        className="relative overflow-hidden rounded-card bg-track"
        style={{ aspectRatio: ASPECT, touchAction: zoom.scale > 1 ? 'none' : 'pan-y' }}
        {...handlers}
      >
        <svg
          viewBox={`${vb.x1} ${vb.y1} ${vbW} ${vb.y2 - vb.y1}`}
          preserveAspectRatio="xMidYMid slice"
          className="absolute inset-0 size-full origin-center transition-transform duration-75"
          style={{ transform: `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.scale})` }}
          role="img"
          aria-label={alt}
        >
          <image href={evidence.raw_url} x={0} y={0} width={evidence.width} height={evidence.height} preserveAspectRatio="none" />
          {outlines && evidence.defect_svg && (
            <g>
              <path d={evidence.defect_svg} fill="none" stroke="var(--color-white)" strokeWidth={6} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
              <path
                d={evidence.defect_svg}
                fill="var(--color-redline)"
                fillOpacity={0.3}
                stroke="var(--color-redline)"
                strokeWidth={3}
                vectorEffect="non-scaling-stroke"
                strokeLinejoin="round"
              />
            </g>
          )}
          {outlines && evidence.pointer && (
            <g>
              <circle cx={evidence.pointer.x} cy={evidence.pointer.y} r={9 * px} fill="var(--color-white)" />
              <circle cx={evidence.pointer.x} cy={evidence.pointer.y} r={6.5 * px} fill="var(--color-tape)" />
            </g>
          )}
        </svg>
        {hasMarks && (
          <button
            type="button"
            onClick={() => setOutlines((o) => !o)}
            aria-pressed={outlines}
            className="absolute right-3 bottom-3 flex h-11 items-center gap-2 rounded-full bg-graphite/80 px-4 text-body font-medium text-white active:bg-graphite"
          >
            {outlines ? <Eye size={18} aria-hidden /> : <EyeOff size={18} aria-hidden />}
            {outlines ? 'Outlines on' : 'Outlines off'}
          </button>
        )}
      </div>
      <span className={cx('absolute -top-2 -left-2 flex')}>
        <TapeTab number={number} badge={badge} />
      </span>
    </div>
  );
}
