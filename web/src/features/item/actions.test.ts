import { describe, expect, it } from 'vitest';
import type { HistoryEntryOut } from '../../api/types';
import { checkerName, groundingLabel, historyText, moreActions, primaryAction, recipient } from './actions';
import { diffPatch } from './EditSheet';
import { diskMock } from '../../test/fixtures';

describe('primary action by status (DESIGN.md "Bottom bar")', () => {
  it.each([
    ['open', 'sign_off', 'Sign off'],
    ['dispatched', 'sign_off', 'Sign off'],
    ['needs_review', 'approve_send', 'Approve and send to Ace Electric'],
    ['closed', 'reopen', 'Reopen'],
  ] as const)('%s -> %s', (status, kind, label) => {
    expect(primaryAction(status, 'Ace Electric')).toEqual({ kind, label });
  });

  it('falls back to the trade in plain words when there is no contact', () => {
    expect(recipient({ trade: 'windows_glazing' }, null)).toBe('Windows & glazing');
  });
});

describe('More sheet', () => {
  it('offers Send again, Mark as needs a look and Edit for an open item that was sent', () => {
    const kinds = moreActions({ status: 'dispatched', dispatch: {} as never }, 'Ace Electric').map((a) => a.label);
    expect(kinds.slice(0, 3)).toEqual(['Send again', 'Mark as needs a look', 'Edit']);
  });

  it('says "Send to <company>" when it was never sent', () => {
    expect(moreActions({ status: 'open', dispatch: null }, 'Ace Electric')[0].label).toBe('Send to Ace Electric');
  });

  it('offers Dismiss item and Edit for Needs a look, and no send', () => {
    const kinds = moreActions({ status: 'needs_review', dispatch: null }, 'X').map((a) => a.kind);
    expect(kinds).toEqual(['dismiss', 'edit', 'share']);
  });

  it("keeps to DESIGN.md's list plus the share link: no Dismiss for Open, only the link for Signed off", () => {
    expect(moreActions({ status: 'open', dispatch: null }, 'X').map((a) => a.kind)).toEqual(['send', 'mark_needs_look', 'edit', 'share']);
    expect(moreActions({ status: 'closed', dispatch: null }, 'X').map((a) => a.kind)).toEqual(['share']);
  });
});

describe('channel and wording', () => {
  it('names how the defect was located, including the model-box value and unknown future ones', () => {
    expect(groundingLabel('sam_concept')).toBe('Outline by SAM 3.1');
    expect(groundingLabel('sam_box')).toBe("Outline from the model's box");
    expect(groundingLabel('spark_box')).toBe('Box from the model');
    expect(groundingLabel('pointed_only')).toBe('Pointed location');
    expect(groundingLabel('none')).toBe('Voice only');
    expect(groundingLabel('something_new')).toBe('Located by the models');
  });

  it('names the checker, including the fallback', () => {
    expect(checkerName('baseten')).toBe('Muse Spark Open on Baseten');
    expect(checkerName('meta-fallback')).toBe('Muse Spark 1.3 (fallback)');
  });

  it('writes human edits as overrides in plain words', () => {
    const h: HistoryEntryOut = {
      at: '2026-11-03T16:00:00Z',
      actor: 'human',
      action: 'edited',
      field: 'trade',
      from: 'carpentry_millwork',
      to: 'general',
      note: null,
    };
    expect(historyText(h)).toBe('You changed the trade from Carpentry & millwork to General.');
    expect(historyText({ ...h, action: 'status_changed', field: 'status', from: 'open', to: 'closed' })).toBe('You signed it off.');
  });

  it('shows the note of a field-less edit (the model writing the item), never "from nothing to nothing"', () => {
    const wrote: HistoryEntryOut = {
      at: '2026-11-04T01:49:00Z',
      actor: 'model',
      action: 'edited',
      field: null,
      from: null,
      to: null,
      note: 'Muse Spark Open wrote the item',
    };
    expect(historyText(wrote)).toBe('Muse Spark Open wrote the item');
    expect(historyText({ ...wrote, note: null })).toBe('Written by Muse Spark Open on Baseten.');
    expect(historyText({ ...wrote, actor: 'human', note: null })).toBe('You edited it.');
    expect(historyText({ ...wrote, field: '' })).not.toMatch(/nothing/);
    // A model edit with a field keeps the "Changed X from A to B" wording.
    expect(historyText({ ...wrote, field: 'severity', from: 'cosmetic', to: 'functional', note: null })).toBe(
      'Changed the severity from Cosmetic to Functional.',
    );
  });
});

describe('edit sheet', () => {
  it('sends only what changed', async () => {
    const item = await diskMock().api.item('itm_2b_07');
    expect(diffPatch(item, { title: item.title, action: item.action, trade: item.trade, severity: item.severity, room: item.room ?? '' })).toEqual({});
    expect(
      diffPatch(item, { title: ' Scuffed wall ', action: item.action, trade: 'drywall', severity: item.severity, room: item.room ?? '' }),
    ).toEqual({ title: 'Scuffed wall', trade: 'drywall' });
  });
});
