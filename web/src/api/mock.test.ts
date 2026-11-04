import { describe, expect, it } from 'vitest';
import { diskMock } from '../test/fixtures';
import { SEGMENT_STATUSES } from './filters';

describe('mock adapter (fixtures/mock_api)', () => {
  it('loads the demo project and rewrites media URLs to the mock server', async () => {
    const { api } = diskMock();
    const { projects } = await api.projects();
    expect(projects.map((p) => p.id)).toContain('prj_unit2b');
    const p = await api.project('prj_unit2b');
    expect(p.name).toBe('Unit 2B final punch');
    expect(p.counts).toMatchObject({ todo: 6, signed_off: 6, all: 12 });
    const item = await api.item('itm_2b_01');
    expect(item.evidence?.crop_url).toBe('/mock-api/media/items/itm_2b_01/crop.jpg');
  });

  it('filters and recounts the unfiltered item list like the API', async () => {
    const { api } = diskMock();
    const all = await api.items('prj_unit2b');
    expect(all.total).toBe(12);
    expect(all.counts).toMatchObject({ todo: 6, needs_review: 2, signed_off: 6, all: 12 });

    const todo = await api.items('prj_unit2b', { status: SEGMENT_STATUSES.todo });
    expect(todo.total).toBe(6);
    expect(todo.facets.trade.electrical).toBe(3); // facets describe the unfiltered list

    const filtered = await api.items('prj_unit2b', { trade: ['electrical'], severity: ['safety'] });
    expect(filtered.items.map((i) => i.number)).toEqual([11]);

    const none = await api.items('prj_unit2b', { trade: ['hvac'] });
    expect(none.total).toBe(0);
  });

  it('applies patches in memory and logs them as human overrides', async () => {
    const { api } = diskMock();
    const before = await api.item('itm_2b_07');
    const after = await api.patchItem('itm_2b_07', { status: 'closed' });
    expect(after.status).toBe('closed');
    expect(after.history.length).toBe(before.history.length + 1);
    expect(after.history.at(-1)).toMatchObject({ actor: 'human', action: 'status_changed', to: 'closed' });
    const list = await api.items('prj_unit2b');
    expect(list.counts.signed_off).toBe(7);
    expect((await api.item('itm_2b_07')).status).toBe('closed');
  });

  it('records a dispatch and marks the items sent', async () => {
    const { api } = diskMock();
    const preview = await api.dispatchPreview('prj_unit2b', 'electrical');
    // Electrical has nothing new to send; send its already-sent items again.
    const ids = preview.already_sent.map((a) => a.item_id);
    expect(ids.length).toBeGreaterThan(0);
    const out = await api.dispatch('prj_unit2b', 'electrical', { channel: 'slack', item_ids: ids });
    expect(out.company).toBe('Ace Electric');
    expect(out.summary).toBe(`Posted ${ids.length} items to #punch-electrical`);
    const history = await api.dispatches('prj_unit2b', 'electrical');
    expect(history.dispatches[0].id).toBe(out.id);
    const item = await api.item(ids[0]);
    expect(item.dispatch?.dispatch_id).toBe(out.id);
  });

  it('plays the processing replay and supports the empty scenarios', async () => {
    const { api, control } = diskMock();
    expect((await api.progress('wlk_3a_first')).status).toBe('running');
    control.setProgressStep(8);
    expect((await api.progress('wlk_3a_first')).status).toBe('done');
    control.setScenario('first_run');
    expect((await api.items('prj_unit2b')).total).toBe(0);
    expect((await api.walks()).walks).toEqual([]);
    control.setScenario('todo_empty');
    expect((await api.items('prj_unit2b')).counts.todo).toBe(0);
  });

  it('answers a missing fixture with a 404', async () => {
    const { api } = diskMock();
    await expect(api.item('itm_nope')).rejects.toBeTruthy();
  });
});
