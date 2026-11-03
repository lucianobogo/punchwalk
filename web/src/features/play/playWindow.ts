// Play this moment: start at video_start_s, pause at video_end_s, "Keep watching" removes the limit.
// The logic lives here, against a tiny video-like interface, so it's tested without a browser.

export interface VideoLike {
  currentTime: number;
  readonly duration: number;
  readonly paused: boolean;
  play(): Promise<void> | void;
  pause(): void;
}

export interface Moment {
  start: number;
  end: number;
}

/** Small tolerance so a frame landing just before the end still counts as the end. */
const END_EPSILON = 0.05;

export class MomentController {
  readonly moment: Moment;
  private video: VideoLike | null = null;
  /** true after "Keep watching": no end limit, scrubber spans the whole video. */
  unlimited = false;
  /** true once playback stopped at the end of the moment. */
  reachedEnd = false;

  constructor(moment: Moment) {
    const start = Math.max(0, moment.start);
    this.moment = { start, end: Math.max(start, moment.end) };
  }

  attach(video: VideoLike) {
    this.video = video;
  }

  /** Seek to the start of the moment (before play). */
  cue() {
    if (!this.video) return;
    this.video.currentTime = this.moment.start;
    this.reachedEnd = false;
  }

  /** Called on every frame (requestVideoFrameCallback) or timeupdate. Returns true when it paused. */
  tick(t: number): boolean {
    if (!this.video || this.unlimited || this.video.paused) return false;
    if (t >= this.moment.end - END_EPSILON) {
      this.video.pause();
      this.reachedEnd = true;
      return true;
    }
    return false;
  }

  /** Play/pause. Playing again after the end of the moment starts the moment over. */
  async toggle() {
    const v = this.video;
    if (!v) return;
    if (!v.paused) {
      v.pause();
      return;
    }
    if (!this.unlimited && (this.reachedEnd || v.currentTime >= this.moment.end - END_EPSILON || v.currentTime < this.moment.start - 0.5)) {
      this.cue();
    }
    this.reachedEnd = false;
    await v.play();
  }

  async replay() {
    if (!this.video) return;
    this.cue();
    await this.video.play();
  }

  /** "Keep watching": lift the limit and continue from where it stopped. */
  async keepWatching() {
    if (!this.video) return;
    this.unlimited = true;
    this.reachedEnd = false;
    await this.video.play();
  }

  /** Scrubber range: the moment, or the whole video after Keep watching. */
  range(duration: number): Moment {
    if (this.unlimited) return { start: 0, end: Number.isFinite(duration) && duration > 0 ? duration : this.moment.end };
    return this.moment;
  }

  seek(t: number) {
    if (!this.video) return;
    const r = this.range(this.video.duration);
    this.video.currentTime = Math.min(r.end, Math.max(r.start, t));
    this.reachedEnd = false;
  }
}

/** The caption shows while the quote is spoken (with a short tail so it doesn't flicker off). */
export function captionVisible(t: number, quoteStart: number | null, quoteEnd: number | null, tail = 0.6): boolean {
  if (quoteStart == null || quoteEnd == null) return false;
  return t >= quoteStart - 0.1 && t <= quoteEnd + tail;
}
