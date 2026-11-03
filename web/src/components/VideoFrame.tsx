// A video box with a fixed aspect and a slot for overlays (hand outline, captions, controls).
// Portrait glasses video is never letterboxed into a landscape frame: the box takes the video's
// own aspect, or crops (cover) into a smaller one. Always playsinline.
import type { ReactNode, Ref } from 'react';
import { cx } from '../lib/cx';

export type VideoAspect = '3/4' | '9/16' | '16/9' | '4/5';

export interface VideoFrameProps {
  src?: string | null;
  poster?: string | null;
  aspect?: VideoAspect;
  /** Fixed height instead of an aspect (walk detail uses ~500 px). */
  height?: number;
  /** Overlays: an absolutely positioned SVG, captions, controls. */
  children?: ReactNode;
  videoRef?: Ref<HTMLVideoElement>;
  muted?: boolean;
  /** Accessible name for the video. */
  label?: string;
  className?: string;
  rounded?: boolean;
}

export function VideoFrame({
  src,
  poster,
  aspect = '3/4',
  height,
  children,
  videoRef,
  muted,
  label = 'Walk video',
  className,
  rounded = false,
}: VideoFrameProps) {
  return (
    <div
      className={cx('relative w-full overflow-hidden bg-night', rounded && 'rounded-image', className)}
      style={height ? { height } : { aspectRatio: aspect }}
    >
      {src ? (
        <video
          ref={videoRef}
          src={src}
          poster={poster ?? undefined}
          playsInline
          preload="metadata"
          muted={muted}
          aria-label={label}
          className="absolute inset-0 size-full object-cover"
        />
      ) : poster ? (
        <img src={poster} alt="" className="absolute inset-0 size-full object-cover" />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center text-secondary text-night-pencil">
          No video for this walk yet.
        </div>
      )}
      {children && <div className="absolute inset-0">{children}</div>}
    </div>
  );
}
