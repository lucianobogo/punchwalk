import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { formatClock, formatCost, formatDuration, formatShortDate, setDisplayTimeZone } from './format';
import {
  SEVERITY_LABEL,
  STATUS_LABEL,
  TRADE_LABEL,
  plural,
  sendLabel,
  statusDetail,
  uiStatus,
} from './labels';

beforeAll(() => setDisplayTimeZone('America/New_York'));
afterAll(() => setDisplayTimeZone(undefined));

describe('labels (DESIGN.md "Words")', () => {
  it('shows three statuses: open and dispatched are both Open', () => {
    expect(STATUS_LABEL.open).toBe('Open');
    expect(STATUS_LABEL.dispatched).toBe('Open');
    expect(STATUS_LABEL.needs_review).toBe('Needs a look');
    expect(STATUS_LABEL.closed).toBe('Signed off');
    expect(uiStatus('dispatched')).toBe('open');
    expect(uiStatus('needs_review')).toBe('needs_look');
    expect(uiStatus('closed')).toBe('signed_off');
  });

  it('uses plain words for severities and trades', () => {
    expect(Object.values(SEVERITY_LABEL)).toEqual(['Cosmetic', 'Functional', 'Safety']);
    expect(TRADE_LABEL.carpentry_millwork).toBe('Carpentry & millwork');
    expect(TRADE_LABEL.doors_hardware).toBe('Doors & hardware');
    expect(TRADE_LABEL.windows_glazing).toBe('Windows & glazing');
    expect(TRADE_LABEL.hvac).toBe('HVAC');
  });

  it('writes sending as a fact about an open item', () => {
    const dispatch = {
      dispatch_id: 'dsp_1',
      trade: 'carpentry_millwork' as const,
      company: 'Oakline Millwork',
      channel: 'email' as const,
      sent_at: '2026-11-03T16:00:00Z',
      times_sent: 1,
    };
    expect(statusDetail({ status: 'dispatched', dispatch })).toBe('Open, sent to Oakline Millwork Nov 3');
    expect(statusDetail({ status: 'open', dispatch: null })).toBe('Open');
    expect(statusDetail({ status: 'closed', dispatch })).toBe('Signed off');
  });

  it('says exactly what a send button does', () => {
    expect(sendLabel(3, 'Ace Electric')).toBe('Send 3 items to Ace Electric');
    expect(sendLabel(1, 'Ace Electric')).toBe('Send 1 item to Ace Electric');
    expect(plural(2, 'open item')).toBe('2 open items');
  });
});

describe('format', () => {
  it('formats video times, durations, money and dates', () => {
    expect(formatClock(14.6)).toBe('0:14');
    expect(formatClock(92)).toBe('1:32');
    expect(formatDuration(112_000)).toBe('1 min 52 s');
    expect(formatDuration(41_000)).toBe('41 s');
    expect(formatCost(0.4312)).toBe('$0.43');
    expect(formatShortDate('2026-11-04T13:05:00Z')).toBe('Nov 4');
  });
});
