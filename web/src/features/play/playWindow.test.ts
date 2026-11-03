import { describe, expect, it } from 'vitest';
import { MomentController, captionVisible, type VideoLike } from './playWindow';
import { frameAt } from '../../components/HandOverlay';
import type { OverlayOut } from '../../api/types';

class FakeVideo implements VideoLike {
  currentTime = 0;
  duration = 150;
  paused = true;
  play() {
    this.paused = false;
  }
  pause() {
    this.paused = true;
  }
}

function setup(start = 11, end = 18) {
  const v = new FakeVideo();
  const c = new MomentController({ start, end });
  c.attach(v);
  return { v, c };
}

describe('Play this moment', () => {
  it('starts at video_start_s', async () => {
    const { v, c } = setup();
    c.cue();
    expect(v.currentTime).toBe(11);
    await c.toggle();
    expect(v.paused).toBe(false);
  });

  it('pauses at video_end_s', async () => {
    const { v, c } = setup();
    c.cue();
    await c.toggle();
    expect(c.tick(15)).toBe(false);
    expect(v.paused).toBe(false);
    expect(c.tick(17.98)).toBe(true);
    expect(v.paused).toBe(true);
    expect(c.reachedEnd).toBe(true);
  });

  it('starts the moment over when play is pressed after it ended', async () => {
    const { v, c } = setup();
    c.cue();
    await c.toggle();
    v.currentTime = 18;
    c.tick(18);
    await c.toggle();
    expect(v.currentTime).toBe(11);
    expect(v.paused).toBe(false);
  });

  it('Keep watching removes the limit and continues from where it stopped', async () => {
    const { v, c } = setup();
    c.cue();
    await c.toggle();
    v.currentTime = 18;
    c.tick(18);
    await c.keepWatching();
    expect(v.paused).toBe(false);
    expect(v.currentTime).toBe(18);
    expect(c.tick(40)).toBe(false);
    expect(c.range(v.duration)).toEqual({ start: 0, end: 150 });
  });

  it('limits the scrubber to the moment until Keep watching', () => {
    const { v, c } = setup();
    expect(c.range(v.duration)).toEqual({ start: 11, end: 18 });
    c.seek(2);
    expect(v.currentTime).toBe(11);
    c.seek(99);
    expect(v.currentTime).toBe(18);
  });

  it('Replay goes back to the start of the moment and plays', async () => {
    const { v, c } = setup();
    v.currentTime = 16;
    await c.replay();
    expect(v.currentTime).toBe(11);
    expect(v.paused).toBe(false);
  });

  it('never starts before 0 or ends before it starts', () => {
    const c = new MomentController({ start: -2, end: -5 });
    expect(c.moment).toEqual({ start: 0, end: 0 });
  });

  it('shows the caption while the quote is spoken', () => {
    expect(captionVisible(13, 14, 17.2)).toBe(false);
    expect(captionVisible(14.5, 14, 17.2)).toBe(true);
    expect(captionVisible(17.6, 14, 17.2)).toBe(true);
    expect(captionVisible(18.5, 14, 17.2)).toBe(false);
    expect(captionVisible(15, null, null)).toBe(false);
  });
});

describe('overlay frame lookup', () => {
  const overlay = {
    fps: 10,
    width: 1080,
    height: 1920,
    frames: [{ t: 1.0, hands: [] }, { t: 1.1, hands: [] }, { t: 5.0, hands: [] }],
  } as unknown as OverlayOut;

  it('finds the nearest frame within one sample, and nothing between points', () => {
    expect(frameAt(overlay, 1.04)?.t).toBe(1.0);
    expect(frameAt(overlay, 1.08)?.t).toBe(1.1);
    expect(frameAt(overlay, 3)).toBeNull();
    expect(frameAt(overlay, 5.05)?.t).toBe(5.0);
    expect(frameAt(undefined, 1)).toBeNull();
  });
});
