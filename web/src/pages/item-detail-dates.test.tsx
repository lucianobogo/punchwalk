// Item detail's before/after captions (DESIGN.md "Item detail", before/after): each photo is labelled with
// the day its walk happened, the same date the Re-walk page shows, not when the item row was written
// (in the seeded demo that's the import time, a day later).
import { render, screen } from '@testing-library/react';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { App } from '../App';
import type { Api } from '../api/api';
import { setApi } from '../api/client';
import { makeQueryClient } from '../api/hooks';
import type { ItemOut, WalkOut } from '../api/types';
import { setDisplayTimeZone } from '../lib/format';
import { routes } from '../router';
import { diskMock } from '../test/fixtures';

beforeAll(() => setDisplayTimeZone('America/New_York'));
afterAll(() => setDisplayTimeZone(undefined));

/** Item 1 is signed off on the re-walk (wlk_2b_first -> wlk_2b_rewalk). */
const ITEM = 'itm_2b_01';

const WALK_DATES: Record<string, Pick<WalkOut, 'recorded_at' | 'created_at'>> = {
  wlk_2b_first: { recorded_at: '2026-10-28T14:00:00Z', created_at: '2026-10-28T15:00:00Z' },
  // No recorded_at: the upload time stands in, as on the server.
  wlk_2b_rewalk: { recorded_at: null, created_at: '2026-10-30T13:00:00Z' },
};

function renderItem({ listWalks = true } = {}) {
  const { api } = diskMock();
  setApi({
    ...api,
    // The item row was written later than either walk (a seed/import time).
    item: async (id: string): Promise<ItemOut> => {
      const it = await api.item(id);
      return { ...it, created_at: '2026-11-04T16:00:00Z', closure: it.closure && { ...it.closure, decided_at: '2026-11-05T16:00:00Z' } };
    },
    walks: async (projectId?: string) => {
      if (!listWalks) return { walks: [] };
      const list = await api.walks(projectId);
      return { walks: list.walks.map((w) => ({ ...w, ...WALK_DATES[w.id] })) };
    },
  } as Api);
  const router = createMemoryRouter(routes, { initialEntries: [`/items/${ITEM}`] });
  return render(<App router={router} client={makeQueryClient()} />);
}

describe('item detail: before/after captions', () => {
  it('uses the dates of the original walk and the re-walk', async () => {
    renderItem();
    expect(await screen.findByText('Oct 28')).toBeInTheDocument();
    expect(screen.getByText('Oct 30, re-walk')).toBeInTheDocument();
    expect(screen.queryByText('Nov 4')).toBeNull();
    expect(screen.queryByText('Nov 5, re-walk')).toBeNull();
  });

  it('falls back to the item’s own dates when the walks aren’t listed', async () => {
    renderItem({ listWalks: false });
    expect(await screen.findByText('Nov 4')).toBeInTheDocument();
    expect(screen.getByText('Nov 5, re-walk')).toBeInTheDocument();
  });
});
