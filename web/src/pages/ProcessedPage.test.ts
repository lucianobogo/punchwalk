import { describe, expect, it } from 'vitest';
import { diskMock } from '../test/fixtures';
import { evalVisibility, summaryLine } from './ProcessedPage';

describe('How this walk was processed', () => {
  it('says the time, cost and how many Meta models, from the model-call log', async () => {
    const p = await diskMock().api.processing('wlk_2b_first');
    expect(summaryLine(p)).toBe('Done in 1 min 52 s for about $0.44, using three Meta models.');
  });

  it('says a replayed walk cost nothing new, instead of "about $0.00"', () => {
    const p = { total_duration_ms: 94_000, total_cost_usd: 0, models_used: ['a', 'b', 'c'], calls: [{ cached: true }, { cached: true }] };
    expect(summaryLine(p)).toBe('Done in 1 min 34 s from recorded model answers (no new cost), using three Meta models.');
  });

  it('never presents mock eval numbers as real', async () => {
    const p = await diskMock().api.processing('wlk_2b_first');
    expect(p.eval?.source).toBe('mock');
    expect(evalVisibility(p.eval, 'mock')).toBe('sample');
    expect(evalVisibility(p.eval, 'live')).toBe('hidden');
    expect(evalVisibility({ ...p.eval!, source: 'eval/report.md' }, 'live')).toBe('real');
    expect(evalVisibility(null, 'mock')).toBe('hidden');
  });
});
