// Walk detail's one orchestrated moment (DESIGN.md "Motion"): when the video reaches the second an item
// was called out, a "New" card slides up over the video, then settles into the list below.
// Driven by video time, not wall time, so it's deterministic under seeks and pauses.
import type { OverlayEventOut } from '../../api/types';

/** How long (in video seconds) the new-item card stays over the video. */
export const CARD_HOLD_S = 2.5;

/** The event whose card is over the video at time t (the latest one inside its hold window), or null. */
export function cardAt(events: OverlayEventOut[], t: number, hold = CARD_HOLD_S): OverlayEventOut | null {
  let best: OverlayEventOut | null = null;
  for (const e of events) {
    if (e.t <= t && t < e.t + hold && (!best || e.t > best.t)) best = e;
  }
  return best;
}

export interface MomentState {
  /** Item over the video right now. */
  card: OverlayEventOut | null;
  /** Items called out so far, including the one in the card ("Items so far N"). */
  soFar: OverlayEventOut[];
  /** Items settled into the list: called out so far, minus the one in the card. Newest first. */
  listed: OverlayEventOut[];
  /** Items later in the walk. */
  later: number;
}

export function momentState(events: OverlayEventOut[], t: number, hold = CARD_HOLD_S): MomentState {
  const sorted = [...events].sort((a, b) => a.t - b.t);
  const soFar = sorted.filter((e) => e.t <= t);
  const card = cardAt(sorted, t, hold);
  const listed = soFar.filter((e) => e !== card).reverse();
  return { card, soFar, listed, later: sorted.length - soFar.length };
}

/** Where a tap on an item (marker or row) seeks: a moment before it was called out, so the hand comes in. */
export function seekTargetFor(e: Pick<OverlayEventOut, 't'>, lead = 1.5): number {
  return Math.max(0, e.t - lead);
}

/**
 * The marker a tap at x (px from the track's left edge) means: the closest one within `tolPx`
 * (half a 44 px target), so neighbouring markers never steal each other's taps. Null = a plain seek.
 */
export function nearestMarker<E extends Pick<OverlayEventOut, 't'>>(
  events: E[],
  x: number,
  widthPx: number,
  durationS: number,
  tolPx = 22,
): E | null {
  if (!durationS || !widthPx) return null;
  let best: E | null = null;
  let bestD = Infinity;
  for (const e of events) {
    const d = Math.abs((e.t / durationS) * widthPx - x);
    if (d <= tolPx && d < bestD) {
      best = e;
      bestD = d;
    }
  }
  return best;
}

/** The transcript line being spoken at t, if any. */
export function activeTurnIndex(turns: { start_ms: number; end_ms: number }[], t: number): number {
  const ms = t * 1000;
  return turns.findIndex((x) => x.start_ms <= ms && ms < x.end_ms);
}
