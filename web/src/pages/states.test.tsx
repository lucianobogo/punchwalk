// Empty, error and loading states (PLAN 8.4): an unreachable API gives a calm card with Try again,
// a missing item says so, and an empty server doesn't leave a skeleton spinning forever.
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { App } from '../App';
import { ApiError, type Api } from '../api/api';
import { setApi } from '../api/client';
import { makeQueryClient } from '../api/hooks';
import { OFFLINE_MESSAGE, liveApi } from '../api/live';
import { routes } from '../router';
import { diskMock } from '../test/fixtures';

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(<App router={router} client={makeQueryClient()} />);
}

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe('live adapter', () => {
  it('turns "no answer at all" into plain words', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Load failed')));
    await expect(liveApi.projects()).rejects.toMatchObject({ status: 0, message: OFFLINE_MESSAGE });
  });

  it('passes the server’s readable detail through', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'This walk is longer than 10 minutes.' }), { status: 413 })),
    );
    await expect(liveApi.walks()).rejects.toMatchObject({ status: 413, message: 'This walk is longer than 10 minutes.' });
  });
});

describe('screen states', () => {
  it('shows a calm error card with Try again when the API is down', async () => {
    const down = { ...diskMock().api, projects: () => Promise.reject(new ApiError(0, OFFLINE_MESSAGE)) } as Api;
    setApi(down);
    renderAt('/');
    expect(await screen.findByText('This didn’t load.', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText(OFFLINE_MESSAGE)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('says so when the server has no project yet, instead of loading forever', async () => {
    setApi({ ...diskMock().api, projects: async () => ({ projects: [] }) } as Api);
    renderAt('/trades');
    expect(await screen.findByText('There’s no project here yet.')).toBeInTheDocument();
  });

  it('shows no demo banner on an empty server, even in a demo session', async () => {
    sessionStorage.setItem('punchwalk.demo', '1');
    setApi({ ...diskMock().api, projects: async () => ({ projects: [] }) } as Api);
    renderAt('/');
    expect(await screen.findByText('There’s no project here yet.')).toBeInTheDocument();
    expect(screen.queryByText('This is a demo project.', { exact: false })).toBeNull();
    sessionStorage.clear();
  });

  it('says "This item isn’t here anymore." for a missing item', async () => {
    setApi(diskMock().api);
    renderAt('/items/itm_gone');
    expect(await screen.findByText('This item isn’t here anymore.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });
});
