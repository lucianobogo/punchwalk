import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { App } from '../App';
import { setApi } from '../api/client';
import { makeQueryClient } from '../api/hooks';
import { routes } from '../router';
import { diskMock } from '../test/fixtures';
import { appUrl, qrDataUrl } from '../features/landing/landing';

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(<App router={router} client={makeQueryClient()} />);
}

beforeEach(() => setApi(diskMock().api));

describe('public share page', () => {
  it('shows the item like item detail, from ShareItemOut alone, with no edit actions', async () => {
    renderAt('/share/item/shr_33dfa14b0c');
    expect(await screen.findByRole('heading', { name: 'Outlet cover cracked' })).toBeInTheDocument();
    expect(screen.getByText('Replace the cover')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Play this moment, 1:52/ })).toBeInTheDocument();
    expect(screen.getByText('Electrical, Ace Electric')).toBeInTheDocument();
    expect(screen.getByText('Safety', { selector: 'span' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sign off|More actions|Edit/ })).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Open in Punchwalk' })).toHaveAttribute('href', '/');
  });

  it('says plainly when a link no longer opens an item', async () => {
    renderAt('/share/item/shr_nope');
    expect(await screen.findByText('This link doesn’t open an item anymore.')).toBeInTheDocument();
  });
});

describe('landing QR code', () => {
  it('opens the app at its own origin', async () => {
    expect(appUrl('https://punchwalk.fly.dev')).toBe('https://punchwalk.fly.dev/');
    expect(appUrl('http://localhost:5173/')).toBe('http://localhost:5173/');
    const url = await qrDataUrl(appUrl('https://punchwalk.fly.dev'), '#1b2430');
    expect(url.startsWith('data:image/svg+xml')).toBe(true);
    expect(decodeURIComponent(url)).toContain('#1b2430');
  });
});
