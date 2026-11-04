import { describe, expect, it } from 'vitest';
import { diskMock } from '../../test/fixtures';
import {
  buildDispatch,
  channelChoices,
  channelLine,
  countsSentence,
  dispatchText,
  effectiveChannelFor,
  headingFor,
  reviewLine,
  sendButtonLabel,
  sentSummary,
  sentWhere,
} from './dispatch';

describe('one vocabulary per trade (DESIGN.md "Trades")', () => {
  it('counts To review, To send and Sent; signed-off items are not counted', async () => {
    const { trades } = await diskMock().api.trades('prj_unit2b');
    const items = (await diskMock().api.items('prj_unit2b')).items;
    for (const t of trades) {
      const mine = items.filter((i) => i.trade === t.trade);
      expect(t.to_review).toBe(mine.filter((i) => i.status === 'needs_review').length);
      expect(t.to_send).toBe(mine.filter((i) => i.status === 'open').length);
      expect(t.sent).toBe(mine.filter((i) => i.status === 'dispatched').length);
      expect(t.caught_up).toBe(t.to_review === 0 && t.to_send === 0);
      expect(t.effective_channel_label).not.toMatch(/Outbox/);
    }
  });

  it('writes the counts as a sentence, never dot-joined', () => {
    expect(countsSentence({ to_review: 1, to_send: 2, sent: 3 })).toBe('1 to review, 2 to send, 3 sent.');
    expect(countsSentence({ to_review: 0, to_send: 2, sent: 0 })).toBe('2 to send.');
    expect(countsSentence({ to_review: 0, to_send: 0, sent: 3 })).toBe('All caught up. 3 sent.');
    expect(countsSentence({ to_review: 0, to_send: 0, sent: 0 })).toBe('All caught up.');
    expect(reviewLine(1)).toBe('1 item needs your review before it can go out');
    expect(reviewLine(2)).toBe('2 items need your review before they can go out');
  });
});

describe('channels', () => {
  it('names channels by what they do, and never says Outbox', () => {
    expect(channelLine({ channel: 'slack', destination: '#punch-electrical' })).toBe('Posts to #punch-electrical.');
    expect(channelLine({ channel: 'slack', destination: null })).toBe('Posts to your team’s Slack channel.');
    expect(channelLine({ channel: 'email', destination: 'maria.chen@example.com' })).toBe('Emails maria.chen@example.com.');
    expect(channelLine({ channel: 'outbox', destination: 'Outbox' })).toBe('Copy a link to share.');
    expect(channelLine({ channel: 'slack', destination: '#x', label: 'From the API.' })).toBe('From the API.');
  });

  it('picks the effective channel: the trade’s own, else the team’s Slack, else Copy link', () => {
    const base = { trade: 'electrical' as const, company: 'Ace', contact: null, phone: null, slack_channel: null, email: null, has_slack_webhook: false };
    expect(effectiveChannelFor({ ...base, has_slack_webhook: true, slack_channel: '#punch-electrical' }).channel).toBe('slack');
    expect(effectiveChannelFor({ ...base, email: 'a@example.com' }, true).channel).toBe('email');
    expect(effectiveChannelFor(base, true)).toEqual({ channel: 'slack', label: 'Posts to your team’s Slack channel.' });
    expect(effectiveChannelFor(base)).toEqual({ channel: 'outbox', label: 'Copy a link to share.' });
  });

  it('offers only working channels, effective first; no picker when there is one', async () => {
    const p = await diskMock().api.dispatchPreview('prj_unit2b', 'electrical');
    const c = channelChoices(p);
    expect(c[0].value).toBe(p.effective_channel);
    expect(c.map((x) => x.label)).toEqual(['Slack', 'Email', 'Copy link']);
    const glass = await diskMock().api.dispatchPreview('prj_unit2b', 'windows_glazing');
    expect(channelChoices(glass).map((x) => x.value)).toEqual(['outbox']);
  });
});

describe('sending', () => {
  it('builds the request from ticked To send items plus re-sends', () => {
    expect(buildDispatch('slack', ['a', 'b'], new Set(), new Set())).toEqual({ channel: 'slack', item_ids: ['a', 'b'] });
    expect(buildDispatch('email', ['a', 'b'], new Set(['a']), new Set(['z']))).toEqual({ channel: 'email', item_ids: ['b', 'z'] });
  });

  it('names the outcome on the button and in history', () => {
    expect(sendButtonLabel('slack', 2, 'Ace Electric')).toBe('Post 2 items to Slack');
    expect(sendButtonLabel('email', 2, 'Brightline Painting')).toBe('Email 2 items to Brightline Painting');
    expect(sendButtonLabel('outbox', 1, 'Clearview Glass')).toBe('Copy a link for 1 item');
    const d = { trade: 'painting' as const, company: 'Brightline Painting', item_ids: ['a', 'b'] };
    expect(sentSummary({ ...d, channel: 'slack' })).toBe('Posted 2 items to Slack');
    expect(sentSummary({ ...d, channel: 'slack', destination: '#punch-painting' })).toBe('Posted 2 items to #punch-painting');
    expect(sentSummary({ ...d, channel: 'email' })).toBe('Emailed 2 items to Brightline Painting');
    expect(sentSummary({ ...d, channel: 'outbox', item_ids: ['a', 'b', 'c'] })).toBe('Copied a link for 3 items');
    expect(sentSummary({ ...d, channel: 'slack', summary: 'Posted 2 items to #punch-electrical' })).toBe('Posted 2 items to #punch-electrical');
    expect(sentWhere('slack', 'Nov 3 at 7:41 PM')).toBe('Sent Nov 3 at 7:41 PM, Slack');
    expect(sentWhere('outbox', 'Nov 3 at 7:41 PM')).toBe('Sent Nov 3 at 7:41 PM, Copy link');
  });

  it('records an in-memory send with the API’s summary wording', async () => {
    const { api } = diskMock();
    const p = await api.dispatchPreview('prj_unit2b', 'windows_glazing');
    const out = await api.dispatch('prj_unit2b', 'windows_glazing', { channel: 'outbox', item_ids: p.item_ids });
    expect(out.summary).toBe(`Copied a link for ${p.item_ids.length} item${p.item_ids.length === 1 ? '' : 's'}`);
    expect(out.channel_label).toBe('Copy link');
    const after = await api.dispatchPreview('prj_unit2b', 'windows_glazing');
    expect(after.lines).toEqual([]);
    expect(after.already_sent.map((a) => a.item_id)).toEqual(p.item_ids);
  });

  it('keeps the heading count true to what is ticked', () => {
    const h = 'Unit 2B final punch: 2 electrical items';
    expect(headingFor(h, 2, 'electrical', 2)).toBe(h);
    expect(headingFor(h, 1, 'electrical', 2)).toBe('Unit 2B final punch: 1 electrical item');
  });

  it('reads a sent message back for the copy button', async () => {
    const list = await diskMock().api.dispatches('prj_unit2b', 'electrical');
    expect(dispatchText(list.dispatches[0])).toMatch(/View item and video/);
  });
});
