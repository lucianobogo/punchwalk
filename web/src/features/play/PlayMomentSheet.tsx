// Play this moment (DESIGN.md, docs/design/03-play-this-moment.png): a dark sheet with the item's tab,
// title and a close button; the portrait video fills the width (3:4) with the hand overlay; the quote
// shows as a caption while it's spoken; playback starts at video_start_s and pauses at video_end_s.
import { Pause, Play } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import type { ItemOut } from '../../api/types';

/** What the sheet needs: an item, or a public share (which has no walk id, so no overlay or walk link). */
export type Playable = Pick<
  ItemOut,
  'number' | 'title' | 'video_url' | 'video_start_s' | 'video_end_s' | 'quote' | 'quote_start_s' | 'quote_end_s'
> & { id: string; origin_walk_id: string | null };
import { useOverlay } from '../../api/hooks';
import { Button, HandOverlay, Sheet, TapeTab } from '../../components';
import { formatClock } from '../../lib/format';
import { useVideoClock } from '../../lib/video';
import { MomentController, captionVisible } from './playWindow';

export function PlayMomentSheet({
  item,
  open,
  onOpenChange,
}: {
  item: Playable | null | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet
      open={open && Boolean(item)}
      onOpenChange={onOpenChange}
      title={item?.title ?? 'Play this moment'}
      tone="night"
      closable
      leading={item ? <TapeTab number={item.number} decorative /> : undefined}
    >
      {item && <PlayBody item={item} />}
    </Sheet>
  );
}

function PlayBody({ item }: { item: Playable }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const ctl = useMemo(() => new MomentController({ start: item.video_start_s, end: item.video_end_s }), [item.id, item.video_start_s, item.video_end_s]);
  const overlay = useOverlay(item.origin_walk_id ?? undefined);
  const [, force] = useState(0);
  const rerender = () => force((n) => n + 1);
  const clock = useVideoClock(videoRef, (t) => {
    if (ctl.tick(t)) rerender();
  });

  // Cue the moment, then try to play: the tap that opened the sheet is the user gesture iOS needs.
  // If the browser still refuses (autoplay policy), the big play button starts it.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    ctl.attach(v);
    let cancelled = false;
    const start = () => {
      if (cancelled) return;
      ctl.cue();
      Promise.resolve(v.play()).catch(() => rerender());
    };
    if (v.readyState >= 1) start();
    else v.addEventListener('loadedmetadata', start, { once: true });
    return () => {
      cancelled = true;
      v.removeEventListener('loadedmetadata', start);
      v.pause();
    };
  }, [ctl]);

  const range = ctl.range(clock.duration);
  const span = Math.max(0.001, range.end - range.start);
  const progress = Math.min(1, Math.max(0, (clock.time - range.start) / span));
  const caption = captionVisible(clock.time, item.quote_start_s, item.quote_end_s) && item.quote;

  if (!item.video_url) {
    return <p className="px-4 pb-8 text-body text-night-pencil">This item has no video. It was added without a walk.</p>;
  }

  return (
    <div className="flex flex-col">
      <div className="relative aspect-[3/4] w-full overflow-hidden bg-night-raised">
        <video
          ref={videoRef}
          src={item.video_url}
          playsInline
          preload="auto"
          className="absolute inset-0 size-full object-cover"
          aria-label={`Video of item ${item.number}, ${item.title}`}
          onClick={() => ctl.toggle()}
        />
        <HandOverlay overlay={overlay.data} time={clock.time} onlyNumber={item.number} />
        {caption && (
          <p className="absolute inset-x-4 bottom-4 rounded-button bg-graphite/85 px-3 py-2.5 text-body text-white" aria-live="polite">
            “{item.quote}”
          </p>
        )}
      </div>

      <div className="px-4 pt-2">
        <input
          type="range"
          aria-label={ctl.unlimited ? 'Position in the walk' : 'Position in this moment'}
          min={range.start}
          max={range.end}
          step={0.05}
          value={Math.min(range.end, Math.max(range.start, clock.time))}
          onChange={(e) => ctl.seek(Number(e.target.value))}
          className="moment-scrubber w-full"
          style={{ ['--fill' as string]: `${progress * 100}%` }}
        />
        <div className="-mt-1 flex justify-between text-body tabular-nums text-night-pencil">
          <span>{formatClock(clock.time)}</span>
          <span>{ctl.unlimited ? formatClock(range.end) : `Moment ends ${formatClock(item.video_end_s)}`}</span>
        </div>
        <div className="mt-2 grid grid-cols-[1fr_auto_1fr] items-center">
          <Button variant="night-quiet" className="justify-self-start text-card font-medium" onClick={() => ctl.replay()}>
            Replay
          </Button>
          <button
            type="button"
            onClick={() => ctl.toggle()}
            aria-label={clock.playing ? 'Pause' : 'Play'}
            className="flex size-14 items-center justify-center rounded-full bg-white text-night active:brightness-90"
          >
            {clock.playing ? <Pause size={24} fill="currentColor" strokeWidth={0} aria-hidden /> : <Play size={24} fill="currentColor" strokeWidth={0} className="translate-x-0.5" aria-hidden />}
          </button>
          <Button
            variant="night-link"
            className="justify-self-end text-card font-medium whitespace-nowrap"
            onClick={() => {
              ctl.keepWatching();
              rerender();
            }}
            disabled={ctl.unlimited}
          >
            Keep watching
          </Button>
        </div>
        {item.origin_walk_id && (
        <p className="text-center">
          <Link
            to={`/walks/${item.origin_walk_id}?t=${Math.floor(item.video_start_s)}`}
            className="inline-flex min-h-11 items-center text-secondary text-night-pencil underline"
          >
            Open the whole walk
          </Link>
        </p>
        )}
      </div>
    </div>
  );
}
