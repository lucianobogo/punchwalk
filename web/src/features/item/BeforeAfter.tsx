// Before/after slider for signed-off items: the photo from the first walk on the left of the handle,
// the re-walk photo (same framing) on the right. Drag anywhere, or use the arrow keys on the handle.
import { ChevronsLeftRight } from 'lucide-react';
import { useRef, useState, type KeyboardEvent, type PointerEvent as RPointerEvent } from 'react';
import { TapeTab, type TapeTabBadge } from '../../components';

export function BeforeAfter({
  before,
  after,
  beforeLabel,
  afterLabel,
  number,
  badge,
  title,
}: {
  before: string;
  after: string;
  beforeLabel: string;
  afterLabel: string;
  number: number;
  badge?: TapeTabBadge;
  title: string;
}) {
  const [pos, setPos] = useState(0.5);
  const box = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const moveTo = (clientX: number) => {
    const r = box.current?.getBoundingClientRect();
    if (!r || r.width === 0) return;
    setPos(Math.min(1, Math.max(0, (clientX - r.left) / r.width)));
  };
  const onDown = (e: RPointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    moveTo(e.clientX);
  };
  const onMove = (e: RPointerEvent<HTMLDivElement>) => {
    if (dragging.current) moveTo(e.clientX);
  };
  const onUp = () => {
    dragging.current = false;
  };
  const onKey = (e: KeyboardEvent) => {
    const step = e.shiftKey ? 0.25 : 0.05;
    if (e.key === 'ArrowLeft') setPos((p) => Math.max(0, p - step));
    else if (e.key === 'ArrowRight') setPos((p) => Math.min(1, p + step));
    else if (e.key === 'Home') setPos(0);
    else if (e.key === 'End') setPos(1);
    else return;
    e.preventDefault();
  };

  const pct = `${pos * 100}%`;
  return (
    <figure className="relative mx-4">
      <div
        ref={box}
        className="relative aspect-[4/3] cursor-ew-resize touch-pan-y overflow-hidden rounded-card bg-track select-none"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
      >
        <img src={after} alt={`${title}, after: ${afterLabel}`} className="absolute inset-0 size-full object-cover" draggable={false} />
        <img
          src={before}
          alt={`${title}, before: ${beforeLabel}`}
          className="absolute inset-0 size-full object-cover"
          style={{ clipPath: `inset(0 calc(100% - ${pct}) 0 0)` }}
          draggable={false}
        />
        <span className="absolute bottom-3 left-3 rounded-full bg-graphite/80 px-2.5 py-1 text-secondary font-medium text-white" style={{ opacity: pos > 0.18 ? 1 : 0 }}>
          Before
        </span>
        <span className="absolute right-3 bottom-3 rounded-full bg-graphite/80 px-2.5 py-1 text-secondary font-medium text-white" style={{ opacity: pos < 0.82 ? 1 : 0 }}>
          After
        </span>
        <div className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-white" style={{ left: pct }} aria-hidden />
        <div
          role="slider"
          tabIndex={0}
          aria-label="Before and after"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pos * 100)}
          aria-valuetext={pos < 0.5 ? 'Showing mostly the after photo' : 'Showing mostly the before photo'}
          onKeyDown={onKey}
          className="absolute top-1/2 flex size-11 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-white text-graphite"
          style={{ left: pct }}
        >
          <ChevronsLeftRight size={20} aria-hidden />
        </div>
      </div>
      <figcaption className="mt-2 flex justify-between text-secondary text-pencil">
        <span>{beforeLabel}</span>
        <span>{afterLabel}</span>
      </figcaption>
      <span className="absolute -top-2 -left-2 flex">
        <TapeTab number={number} badge={badge} />
      </span>
    </figure>
  );
}
