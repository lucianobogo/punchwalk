// The public share page's before/after captions match item detail: the after photo is labelled with the
// re-walk's date (ShareItemOut.after_at), not when the closure row was written (closure.decided_at, which
// in the seeded demo is the import time, a day later). Older payloads without after_at fall back.
import { render, screen } from '@testing-library/react';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { App } from '../App';
import type { Api } from '../api/api';
import { setApi } from '../api/client';
import { makeQueryClient } from '../api/hooks';
import type { ShareItemOut } from '../api/types';
import { setDisplayTimeZone } from '../lib/format';
import { routes } from '../router';
import { diskMock } from '../test/fixtures';

beforeAll(() => setDisplayTimeZone('America/New_York'));
afterAll(() => setDisplayTimeZone(undefined));

const TOKEN = 'shr_55bf15051c'; // "Loose towel bar", signed off on the re-walk

function renderShare(patch: (s: ShareItemOut) => ShareItemOut) {
  const { api } = diskMock();
  setApi({ ...api, share: async (token: string) => patch(await api.share(token)) } as Api);
  const router = createMemoryRouter(routes, { initialEntries: [`/share/item/${TOKEN}`] });
  return render(<App router={router} client={makeQueryClient()} />);
}

const withDates = (after_at: string | null | undefined) => (s: ShareItemOut): ShareItemOut => {
  const { after_at: _drop, ...rest } = s;
  const base = after_at === undefined ? rest : { ...rest, after_at }; // undefined: a payload from before after_at
  return {
    ...base,
    walked_at: '2026-10-28T14:00:00Z',
    closure: s.closure && { ...s.closure, decided_at: '2026-11-05T16:00:00Z' },
  } as ShareItemOut;
};

describe('share page: before/after captions', () => {
  it('labels the after photo with the re-walk date', async () => {
    renderShare(withDates('2026-10-30T13:00:00Z'));
    expect(await screen.findByText('Oct 30, re-walk')).toBeInTheDocument();
    expect(screen.getAllByText('Oct 28').length).toBeGreaterThan(0); // before caption (and the detail list)
    expect(screen.queryByText('Nov 5, re-walk')).toBeNull();
  });

  it('falls back to the decision time when the payload has no after_at', async () => {
    renderShare(withDates(undefined));
    expect(await screen.findByText('Nov 5, re-walk')).toBeInTheDocument();
  });
});
