// Video helpers shared by Play this moment and walk detail: a frame-accurate clock
// (requestVideoFrameCallback, falling back to timeupdate) and the object-fit: cover mapping
// from video pixels to the rendered box (for placing number tabs over the video).
import { useEffect, useLayoutEffect, useState, type RefObject } from 'react';

type RVFCVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number;
  cancelVideoFrameCallback?: (id: number) => void;
};

export interface VideoClock {
  time: number;
  duration: number;
  playing: boolean;
}

/**
 * Current time, duration and play state of a <video>. `onFrame` runs on every presented frame
 * (or timeupdate where rVFC isn't available), before React state updates.
 */
export function useVideoClock(ref: RefObject<HTMLVideoElement | null>, onFrame?: (t: number) => void, deps: unknown[] = []): VideoClock {
  const [clock, setClock] = useState<VideoClock>({ time: 0, duration: 0, playing: false });

  useEffect(() => {
    const v = ref.current as RVFCVideo | null;
    if (!v) return;
    let rvfc = 0;
    let stopped = false;
    const update = (t: number) => {
      onFrame?.(t);
      setClock({ time: t, duration: v.duration || 0, playing: !v.paused });
    };
    const onFrameCb = (_now: number, meta: { mediaTime: number }) => {
      if (stopped) return;
      update(meta.mediaTime);
      rvfc = v.requestVideoFrameCallback!(onFrameCb);
    };
    const onTime = () => update(v.currentTime);
    const hasRvfc = typeof v.requestVideoFrameCallback === 'function';
    if (hasRvfc) rvfc = v.requestVideoFrameCallback!(onFrameCb);
    // timeupdate as well: rVFC doesn't fire while paused, so seeks and pauses still update.
    const events = ['timeupdate', 'seeked', 'play', 'pause', 'loadedmetadata', 'durationchange', 'ended'];
    for (const e of events) v.addEventListener(e, onTime);
    onTime();
    return () => {
      stopped = true;
      if (hasRvfc && v.cancelVideoFrameCallback) v.cancelVideoFrameCallback(rvfc);
      for (const e of events) v.removeEventListener(e, onTime);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ref, ...deps]);

  return clock;
}

/** Rendered size of an element, kept current with ResizeObserver. */
export function useElementSize(ref: RefObject<HTMLElement | null>) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const set = () => setSize({ width: el.clientWidth, height: el.clientHeight });
    set();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

/** Maps a point in a W x H source to the box it's drawn in with object-fit: cover (xMidYMid slice). */
export function coverMap(box: { width: number; height: number }, W: number, H: number) {
  const s = Math.max(box.width / W, box.height / H) || 0;
  const ox = (box.width - W * s) / 2;
  const oy = (box.height - H * s) / 2;
  return { scale: s, x: (x: number) => ox + x * s, y: (y: number) => oy + y * s };
}
