import { beforeAll, describe, expect, it } from 'vitest';
import type { ItemOut } from '../../api/types';
import { diskMock } from '../../test/fixtures';
import { groupItems, rowSummary } from './group';
import { countMatching, facetOptions } from './sheets';
import { DEFAULT_VIEW, clearFilters, hasFilters, viewFromParams, viewToParams, type ListView } from './view';

let items: ItemOut[] = [];
let rooms: string[] = [];

beforeAll(async () => {
  const { api } = diskMock();
  items = (await api.items('prj_unit2b')).items;
  rooms = (await api.project('prj_unit2b')).rooms;
});

const todo = () => items.filter((i) => i.status !== 'closed');

describe('URL view state', () => {
  it('is empty for the default view, so "/" is To do grouped by room', () => {
    expect(viewToParams(DEFAULT_VIEW).toString()).toBe('');
    expect(viewFromParams(new URLSearchParams(''))).toEqual(DEFAULT_VIEW);
  });

  it('round-trips every filter, with repeated keys for OR', () => {
    const v: ListView = {
      segment: 'all',
      filters: { trade: ['electrical', 'painting'], severity: ['safety'], room: ['Living room'], q: 'knob', sort: 'severity' },
      group: 'trade',
      sent: 'no',
    };
    const qs = viewToParams(v).toString();
    expect(qs).toContain('trade=electrical&trade=painting');
    expect(qs).toContain('room=Living+room');
    expect(viewFromParams(new URLSearchParams(qs))).toEqual(v);
  });

  it('ignores unknown values instead of breaking the list', () => {
    const v = viewFromParams(new URLSearchParams('show=nope&group=floor&sent=maybe&sort=random'));
    expect(v).toEqual(DEFAULT_VIEW);
  });

  it('knows when filters are active and clears them but keeps the segment and sort', () => {
    const v: ListView = { ...DEFAULT_VIEW, segment: 'all', filters: { trade: ['electrical'], sort: 'severity' } };
    expect(hasFilters(v)).toBe(true);
    const c = clearFilters(v);
    expect(hasFilters(c)).toBe(false);
    expect(c.segment).toBe('all');
    expect(c.filters.sort).toBe('severity');
  });
});

describe('sections', () => {
  it('pins Needs a look first, then rooms in walk order', () => {
    const sections = groupItems(todo(), { group: 'room', roomOrder: rooms });
    expect(sections[0]).toMatchObject({ key: 'needs_look', title: 'Needs a look', tone: 'hold' });
    expect(sections[0].items.every((i) => i.status === 'needs_review')).toBe(true);
    const roomTitles = sections.slice(1).map((s) => s.title!);
    const order = roomTitles.map((r) => rooms.indexOf(r));
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(sections.slice(1).flatMap((s) => s.items).some((i) => i.status === 'needs_review')).toBe(false);
  });

  it('lists walk order flat (still with Needs a look first) and sorts by severity on request', () => {
    const flat = groupItems(todo(), { group: 'walk' });
    expect(flat.map((s) => s.title)).toEqual(['Needs a look', null]);
    const nums = flat[1].items.map((i) => i.number);
    expect(nums).toEqual([...nums].sort((a, b) => a - b));
    const bySeverity = groupItems(todo(), { group: 'walk', sort: 'severity' });
    expect(bySeverity[1].items[0].severity).toBe('safety');
  });

  it('groups by trade in plain words', () => {
    const titles = groupItems(items, { group: 'trade' }).map((s) => s.title);
    expect(titles).toContain('Electrical');
    expect(titles.every((t) => t && !t.includes('_'))).toBe(true);
  });

  it('writes the row summary as sentences, with the reason for Needs a look', () => {
    const needs = items.find((i) => i.status === 'needs_review')!;
    expect(rowSummary(needs, 'room')).toBe(needs.review_reason);
    const scuff = items.find((i) => i.title === 'Scuff marks on wall')!;
    expect(rowSummary(scuff, 'room')).toBe('Painting. Left of the closet door.');
    expect(rowSummary(scuff, 'trade')).toBe('Bedroom. Left of the closet door.');
  });
});

describe('filter sheet counts', () => {
  it('counts options within the segment and the other filters ("Electrical 2", "Show 2 items")', () => {
    const view: ListView = { ...DEFAULT_VIEW, filters: { severity: ['safety'] } };
    const trades = facetOptions(items, view, 'trade');
    expect(trades).toEqual([{ value: 'electrical', label: 'Electrical', count: 1 }]);
    const electricalTodo = todo().filter((i) => i.trade === 'electrical').length;
    expect(countMatching(items, { ...DEFAULT_VIEW, filters: { trade: ['electrical'] } })).toBe(electricalTodo);
  });

  it('keeps selected values listed even when nothing matches them', () => {
    const view: ListView = { ...DEFAULT_VIEW, filters: { trade: ['hvac'] } };
    expect(facetOptions(items, view, 'trade').find((o) => o.value === 'hvac')).toMatchObject({ count: 0, label: 'HVAC' });
  });
});
