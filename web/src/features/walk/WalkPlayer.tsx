// Walk detail's video (DESIGN.md "Walks", 04-walk-detail.png): portrait video ~500 px tall with the hand
// outlined and the number tab at the fingertip; compact controls over its bottom (play/pause, a timeline
// with a marker per item, the time); the "New" card at each item's moment. When the page scrolls past
// it, the same <video> pins to the top as a 16:9 mini-player (cover crop); tap it to expand.
import { Pause, Play } from 'lucide-react';
import { useEffect, useRef, useState, type PointerEvent as RPointerEvent, type RefObject } from 'react';
import { Link } from 'react-router';
import type { ItemOut, OverlayEventOut, OverlayOut, WalkOut } from '../../api/types';
import { HandOverlay, TapeTab } from '../../components';
import { formatClock } from '../../lib/format';
import { TRADE_LABEL } from '../../lib/labels';
import { cx } from '../../lib/cx';
import { useVideoClock } from '../../lib/video';
import { nearestMarker, seekTargetFor, type MomentState } from './moments';

export const HERO_HEIGHT = 500;

export function itemLine(item: Pick<ItemOut, 'trade' | 'location_detail'>): string {
  const where = item.location_detail ? ` ${item.location_detail[0].toUpperCase()}${item.location_detail.slice(1)}.` : '';
  return `${TRADE_LABEL[item.trade]}.${where}`;
}

export function WalkPlayer({
  walk,
  overlay,
  videoRef,
  moments,
  itemsById,
  startAt,
  onTime,
  onCollapsedChange,
}: {
  walk: WalkOut;
  overlay: OverlayOut | undefined;
  videoRef: RefObject<HTMLVideoElement | null>;
  moments: MomentState;
  itemsById: Map<string, ItemOut>;
  /** ?t= deep link: seconds to seek to once the video can. */
  startAt: number | null;
  onTime: (t: number) => void;
  /** Told when the video collapses into the mini-player (the page then lists the card's item too). */
  onCollapsedChange?: (collapsed: boolean) => void;
}) {
  const slot = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(false);
  const clock = useVideoClock(videoRef, onTime);
  const duration = clock.duration || walk.duration_s || overlay?.duration_s || 0;

  // Collapse into the mini-player once most of the hero has scrolled away.
  useEffect(() => {
    const el = slot.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(
      ([e]) => {
        const c = e.intersectionRatio < 0.45;
        setCollapsed(c);
        onCollapsedChange?.(c);
      },
      { threshold: [0, 0.45, 1] },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [onCollapsedChange]);

  // Deep link: seek once metadata is in.
  useEffect(() => {
    const v = videoRef.current;
    if (!v || startAt == null) return;
    const go = () => {
      v.currentTime = Math.min(startAt, v.duration || startAt);
    };
    if (v.readyState >= 1) go();
    else v.addEventListener('loadedmetadata', go, { once: true });
    return () => v.removeEventListener('loadedmetadata', go);
  }, [videoRef, startAt]);

  const seek = (t: number) => {
    const v = videoRef.current;
    if (v) v.currentTime = Math.max(0, Math.min(duration || t, t));
  };
  const toggle = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) Promise.resolve(v.play()).catch(() => {});
    else v.pause();
  };

  const card = moments.card ? itemsById.get(moments.card.item_id) : undefined;

  return (
    <div ref={slot} className="relative" style={{ height: HERO_HEIGHT }}>
      <div
        className={cx(
          'overflow-hidden bg-night',
          collapsed
            ? 'fixed inset-x-0 top-0 z-30 mx-auto aspect-video max-w-[var(--app-width)] animate-fade-in border-b border-hairline pt-safe'
            : 'absolute inset-0',
        )}
      >
        <div className="relative size-full">
          <video
            ref={videoRef}
            src={walk.media.display_mp4 ?? undefined}
            poster={walk.media.poster ?? undefined}
            playsInline
            preload="metadata"
            aria-label={`Video of the ${walk.kind === 'rewalk' ? 're-walk' : 'walk'}`}
            className="absolute inset-0 size-full object-cover"
          />
          <HandOverlay overlay={overlay} time={clock.time} />
          {/* Tap the video: play/pause, or expand the mini-player. */}
          <button
            type="button"
            className="absolute inset-0 size-full"
            aria-label={collapsed ? 'Show the full video' : clock.playing ? 'Pause' : 'Play'}
            onClick={() => (collapsed ? window.scrollTo({ top: 0, behavior: 'smooth' }) : toggle())}
          />
          {card && moments.card && !collapsed && <NewItemCard key={moments.card.item_id} item={card} />}
          <Controls
            playing={clock.playing}
            time={clock.time}
            duration={duration}
            events={overlay?.events ?? []}
            itemsById={itemsById}
            onToggle={toggle}
            onSeek={seek}
            compact={collapsed}
          />
        </div>
      </div>
    </div>
  );
}

function NewItemCard({ item }: { item: ItemOut }) {
  return (
    <Link
      to={`/items/${item.id}`}
      className="absolute inset-x-3 bottom-[76px] z-10 flex animate-slide-up items-center gap-3.5 rounded-card bg-sheet px-4 py-3.5 shadow-lift"
    >
      <TapeTab number={item.number} pressOn decorative />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-card font-semibold text-graphite">{item.title}</span>
        <span className="block truncate text-body text-pencil">{itemLine(item)}</span>
      </span>
      <span className="shrink-0 text-body font-semibold text-tape-ink">New</span>
      <span className="sr-only" role="status">
        New item {item.number}: {item.title}
      </span>
    </Link>
  );
}

function Controls({
  playing,
  time,
  duration,
  events,
  itemsById,
  onToggle,
  onSeek,
  compact,
}: {
  playing: boolean;
  time: number;
  duration: number;
  events: OverlayEventOut[];
  itemsById: Map<string, ItemOut>;
  onToggle: () => void;
  onSeek: (t: number) => void;
  compact: boolean;
}) {
  const track = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const pct = (t: number) => `${duration > 0 ? Math.min(100, (t / duration) * 100) : 0}%`;
  const seekAt = (clientX: number) => {
    const r = track.current?.getBoundingClientRect();
    if (!r || !duration) return;
    onSeek(((clientX - r.left) / r.width) * duration);
  };
  const down = (e: RPointerEvent<HTMLDivElement>) => {
    const r = track.current?.getBoundingClientRect();
    // A tap near a marker (within half a 44 px target) jumps to that item; the closest marker wins.
    const hit = r ? nearestMarker(events, e.clientX - r.left, r.width, duration) : null;
    if (hit) {
      onSeek(seekTargetFor(hit));
      return;
    }
    dragging.current = true;
    e.currentTarget.setPointerCapture(e.pointerId);
    seekAt(e.clientX);
  };

  return (
    <div className={cx('absolute inset-x-0 bottom-0 z-10 flex items-center gap-3 px-3', compact ? 'pb-2' : 'pb-3')}>
      <button
        type="button"
        onClick={onToggle}
        aria-label={playing ? 'Pause' : 'Play'}
        className="flex size-11 shrink-0 items-center justify-center rounded-full bg-graphite/85 text-white active:bg-graphite"
      >
        {playing ? (
          <Pause size={18} fill="currentColor" strokeWidth={0} aria-hidden />
        ) : (
          <Play size={18} fill="currentColor" strokeWidth={0} className="translate-x-px" aria-hidden />
        )}
      </button>
      <div
        ref={track}
        role="slider"
        tabIndex={0}
        aria-label="Position in the walk"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(time)}
        aria-valuetext={formatClock(time)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight') onSeek(time + 5);
          else if (e.key === 'ArrowLeft') onSeek(time - 5);
          else return;
          e.preventDefault();
        }}
        onPointerDown={down}
        onPointerMove={(e) => dragging.current && seekAt(e.clientX)}
        onPointerUp={() => (dragging.current = false)}
        onPointerCancel={() => (dragging.current = false)}
        className="relative flex h-11 min-w-0 flex-1 cursor-pointer touch-none items-center"
      >
        <div className="relative h-1.5 w-full rounded-full bg-white/55">
          <div className="absolute inset-y-0 left-0 rounded-full bg-white" style={{ width: pct(time) }} />
        </div>
        {events.map((e) => {
          const item = itemsById.get(e.item_id);
          return (
            <button
              key={e.item_id}
              type="button"
              aria-label={`Item ${e.number}${item ? `, ${item.title}` : ''}, ${formatClock(e.t)}`}
              // Taps are resolved by the track (closest marker wins); the button keeps keyboard and
              // screen-reader access with a full 44 x 44 target.
              onClick={() => onSeek(seekTargetFor(e))}
              className="pointer-events-none absolute top-1/2 flex size-11 -translate-x-1/2 -translate-y-1/2 items-center justify-center"
              style={{ left: pct(e.t) }}
            >
              <span className="h-4 w-1 rounded-full bg-tape-tab ring-1 ring-tape/60" />
            </button>
          );
        })}
      </div>
      <span className="shrink-0 rounded-button bg-graphite/85 px-2.5 py-1 text-body font-medium tabular-nums text-white">{formatClock(time)}</span>
    </div>
  );
}
