import { describe, expect, it } from 'vitest';
import { diskMock } from '../../test/fixtures';
import { buildDispatch, channelNote, channelOptions, destinationLine, dispatchText, headingFor, sendButtonLabel, sentToast } from './dispatch';

describe('dispatch request', () => {
  it('always carries the channel and the item ids, in the preview order, minus unticked ones', async () => {
    const p = await diskMock().api.dispatchPreview('prj_unit2b', 'electrical');
    expect(buildDispatch('slack', p)).toEqual({ channel: 'slack', item_ids: p.item_ids });
    expect(buildDispatch('email', p, new Set([p.item_ids[0]]))).toEqual({ channel: 'email', item_ids: p.item_ids.slice(1) });
  });
});

describe('channels', () => {
  it('offers Slack, Email and Copy link, with the API default and its reason', async () => {
    const p = await diskMock().api.dispatchPreview('prj_unit2b', 'electrical');
    const opts = channelOptions(p);
    expect(opts.map((o) => [o.value, o.label, o.disabled])).toEqual([
      ['slack', 'Slack', false],
      ['email', 'Email', false],
      ['outbox', 'Copy link', false],
    ]);
    expect(p.channel).toBe('slack');
    expect(channelNote(p, opts[0])).toBe(p.channel_reason);
    expect(channelNote(p, opts[1])).toBe('Goes to maria.chen@example.com.');
    expect(destinationLine('slack', '#punch-electrical')).toBe('Message to #punch-electrical');
  });

  it('falls back to the Outbox, ready to share, when a trade has no channel', async () => {
    const p = await diskMock().api.dispatchPreview('prj_unit2b', 'windows_glazing');
    const opts = channelOptions(p);
    expect(opts.filter((o) => !o.disabled).map((o) => o.value)).toEqual(['outbox']);
    expect(opts.find((o) => o.value === 'email')?.reason).toMatch(/No email on file/);
    expect(p.channel).toBe('outbox');
    expect(channelNote(p, opts[2])).toMatch(/goes to the Outbox, ready to share/);
    expect(sendButtonLabel('outbox', 1, 'Clearview Glass')).toBe('Copy 1 item for Clearview Glass');
  });

  it('says what happened in the toast', () => {
    const d = { trade: 'electrical' as const, company: 'Ace Electric', item_ids: ['a', 'b', 'c'] };
    expect(sentToast({ ...d, channel: 'slack' })).toBe('Sent 3 items to Ace Electric in Slack');
    expect(sentToast({ ...d, channel: 'email' })).toBe('Sent 3 items to Ace Electric by email');
    expect(sentToast({ ...d, channel: 'outbox' }, true)).toBe('Copied 3 items for Ace Electric. It’s in the Outbox too.');
    expect(sendButtonLabel('slack', 3, 'Ace Electric')).toBe('Send 3 items to Ace Electric');
  });

  it('keeps the heading count true to what is ticked', () => {
    const h = 'Unit 2B final punch: 2 electrical items';
    expect(headingFor(h, 2, 'electrical', 2)).toBe(h);
    expect(headingFor(h, 1, 'electrical', 2)).toBe('Unit 2B final punch: 1 electrical item');
    expect(headingFor('Something else', 1, 'electrical', 2)).toBe('Something else');
  });

  it('reads a sent message back for the Outbox copy button', async () => {
    const list = await diskMock().api.dispatches('prj_unit2b', 'electrical');
    expect(dispatchText(list.dispatches[0])).toMatch(/View item and video/);
    expect(dispatchText({ payload: {} })).toBeNull();
  });
});
