import { describe, expect, it } from 'vitest';
import { REVIEW_FALSE_FIX } from '../../api/types';
import { diskMock } from '../../test/fixtures';
import { barLabel, barSegments, openByTrade, rewalkSections } from './summary';

describe('re-walk summary', () => {
  it('puts the flagged item first, then signed off, still open and not checked', async () => {
    const s = await diskMock().api.rewalkSummary('wlk_2b_rewalk');
    expect(s.headline).toBe('6 signed off. 3 still open. 1 needs a look.');
    const secs = rewalkSections(s);
    expect(secs.map((x) => [x.kind, x.items.length])).toEqual([
      ['needs_a_look', 1],
      ['signed_off', 6],
      ['still_open', 3],
      ['not_checked', 1],
    ]);
    expect(secs[0].items[0].reason).toBe(REVIEW_FALSE_FIX);
    const nums = secs[1].items.map((i) => i.number);
    expect(nums).toEqual([...nums].sort((a, b) => a - b));
  });

  it('draws the bar in proportion and says it in words', async () => {
    const s = await diskMock().api.rewalkSummary('wlk_2b_rewalk');
    const seg = barSegments(s.counts);
    expect(seg.map((x) => x.key)).toEqual(['signed_off', 'still_open', 'needs_a_look', 'not_checked']);
    expect(seg.reduce((n, x) => n + x.fraction, 0)).toBeCloseTo(1);
    expect(barLabel(s.counts)).toBe('Of 11 items: 6 signed off, 3 still open, 1 needs a look, 1 not checked.');
  });

  it('groups still-open items by trade for "send again" (one message per trade)', async () => {
    const s = await diskMock().api.rewalkSummary('wlk_2b_rewalk');
    const g = openByTrade(s.still_open);
    expect([...g.keys()].sort()).toEqual(['electrical', 'painting', 'windows_glazing']);
    expect(g.get('electrical')).toEqual(['itm_2b_08']);
  });
});
