import { describe, expect, it } from 'vitest';
import { frameAt } from '../../components/HandOverlay';
import { diskMock } from '../../test/fixtures';
import { CARD_HOLD_S, activeTurnIndex, cardAt, momentState, seekTargetFor } from './moments';

const ev = (t: number, n: number) => ({ t, item_id: `itm_${n}`, number: n });
const events = [ev(14, 1), ev(27, 2), ev(92, 6)];

describe('new-item card scheduling', () => {
  it('shows the card at the moment, holds it ~2.5 s, then settles it into the list', () => {
    expect(cardAt(events, 13.9)).toBeNull();
    expect(cardAt(events, 14)?.number).toBe(1);
    expect(cardAt(events, 14 + CARD_HOLD_S - 0.01)?.number).toBe(1);
    expect(cardAt(events, 14 + CARD_HOLD_S)).toBeNull();

    const during = momentState(events, 93);
    expect(during.card?.number).toBe(6);
    expect(during.soFar.map((e) => e.number)).toEqual([1, 2, 6]); // "Items so far 3" counts the card
    expect(during.listed.map((e) => e.number)).toEqual([2, 1]); // newest first, card not listed yet

    const after = momentState(events, 92 + CARD_HOLD_S + 0.1);
    expect(after.card).toBeNull();
    expect(after.listed.map((e) => e.number)).toEqual([6, 2, 1]); // settled
    expect(after.later).toBe(0);
  });

  it('is driven by video time, so seeking back hides later items again', () => {
    expect(momentState(events, 20).soFar.map((e) => e.number)).toEqual([1]);
    expect(momentState(events, 20).later).toBe(2);
    expect(momentState(events, 0).soFar).toEqual([]);
  });

  it('picks the latest card when two moments overlap', () => {
    expect(cardAt([ev(10, 1), ev(11, 2)], 11.5)?.number).toBe(2);
  });

  it('seeks a moment before the call-out, never below zero', () => {
    expect(seekTargetFor({ t: 92 })).toBe(90.5);
    expect(seekTargetFor({ t: 0.5 })).toBe(0);
  });

  it('finds the transcript line being spoken', () => {
    const turns = [
      { start_ms: 1000, end_ms: 2500 },
      { start_ms: 3000, end_ms: 6000 },
    ];
    expect(activeTurnIndex(turns, 1.2)).toBe(0);
    expect(activeTurnIndex(turns, 2.8)).toBe(-1);
    expect(activeTurnIndex(turns, 5.9)).toBe(1);
  });
});

describe('overlay against the mock walk', () => {
  it('draws the hand with a fingertip tab while pointing, and nothing between points', async () => {
    const overlay = await diskMock().api.overlay('wlk_2b_first');
    const pointing = frameAt(overlay, 92.8);
    const hand = pointing?.hands.find((h) => h.number === 6);
    expect(hand?.fingertip).not.toBeNull();
    // Between points there are no frames, so nothing is drawn.
    expect(frameAt(overlay, 97)).toBeNull();
    // Every event has a frame with that item's hand nearby.
    for (const e of overlay.events) {
      const near = overlay.frames.filter((f) => Math.abs(f.t - e.t) < 2 && f.hands.some((h) => h.number === e.number));
      if (e.number !== 11) expect(near.length).toBeGreaterThan(0); // #11 was described without pointing
    }
  });
});
