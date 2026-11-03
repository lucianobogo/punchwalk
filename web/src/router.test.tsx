import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { App } from './App';
import { setApi } from './api/client';
import { makeQueryClient } from './api/hooks';
import { routes } from './router';
import { diskMock } from './test/fixtures';

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(<App router={router} client={makeQueryClient()} />);
}

beforeEach(() => {
  setApi(diskMock().api);
  localStorage.clear();
});

describe('router', () => {
  it('opens the punch list at / with the project name and the tab bar', async () => {
    renderAt('/');
    expect(await screen.findByRole('heading', { name: 'Unit 2B final punch' })).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    for (const tab of ['Punch list', 'Walks', 'Record', 'Trades']) {
      expect(nav).toHaveTextContent(tab);
    }
    expect(screen.getByRole('link', { name: /Punch list/ })).toHaveAttribute('aria-current', 'page');
  });

  it.each([
    ['/items/itm_2b_01', 'Missing cabinet knob'],
    ['/walks/wlk_2b_first', 'First walk'],
    ['/walks/wlk_2b_rewalk/rewalk', 'Re-walk'],
    ['/walks/wlk_2b_first/processing', 'How this walk was processed'],
    ['/trades/electrical', 'Ace Electric'],
    ['/walks', 'Walks'],
    ['/record', 'Record a walk'],
    ['/trades', 'Trades'],
  ])('renders %s', async (path, heading) => {
    renderAt(path);
    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument();
  });

  it('renders the component gallery', async () => {
    renderAt('/dev/components');
    expect(await screen.findByRole('heading', { name: 'Components' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Tape tab' })).toBeInTheDocument();
  });

  it('says plainly when a link goes nowhere', async () => {
    renderAt('/nowhere');
    expect(await screen.findByText('There’s no screen here.')).toBeInTheDocument();
  });
});
