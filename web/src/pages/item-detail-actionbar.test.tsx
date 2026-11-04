// Item detail's bottom bar (DESIGN.md "Item detail", "Bottom bar"). The primary button names the outcome,
// so on a needs-a-look item it carries the company ("Approve and send to Northline Appliance Service",
// 47 characters). It must never clip: long labels wrap inside the button, next to the unchanged More button.
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { App } from '../App';
import type { Api } from '../api/api';
import { setApi } from '../api/client';
import { makeQueryClient } from '../api/hooks';
import type { HistoryEntryOut } from '../api/types';
import { routes } from '../router';
import { diskMock } from '../test/fixtures';

const LONG = 'Northline Appliance Service';

/** itm_2b_12 (general trade, needs a look) with a long company and the pipeline's field-less edit. */
function renderLongLabel(company = LONG) {
  const { api } = diskMock();
  setApi({
    ...api,
    project: async (id: string) => {
      const p = await api.project(id);
      return { ...p, trades: p.trades.map((t) => (t.trade === 'general' ? { ...t, company } : t)) };
    },
    item: async (id: string) => {
      const item = await api.item(id);
      const wrote: HistoryEntryOut = {
        at: item.history[0].at,
        actor: 'model',
        action: 'edited',
        field: null,
        from: null,
        to: null,
        note: 'Muse Spark Open wrote the item',
      };
      return { ...item, history: [item.history[0], wrote, ...item.history.slice(1)] };
    },
  } as Api);
  const router = createMemoryRouter(routes, { initialEntries: ['/items/itm_2b_12'] });
  return render(<App router={router} client={makeQueryClient()} />);
}

describe('item detail: bottom bar and History', () => {
  it('lets a long primary label wrap instead of clipping it', async () => {
    renderLongLabel();
    const button = await screen.findByRole('button', { name: `Approve and send to ${LONG}` });
    expect(button).toHaveTextContent(`Approve and send to ${LONG}`);
    expect(button.className).not.toMatch(/whitespace-nowrap|truncate|text-ellipsis|overflow-hidden/);
    expect(button.className).toMatch(/text-balance/);
    expect(button.className).toMatch(/text-body!/);
    expect(screen.getByRole('button', { name: 'More actions' })).toBeInTheDocument();
  });

  it('keeps a short label at the card size', async () => {
    renderLongLabel('Ace');
    const button = await screen.findByRole('button', { name: 'Approve and send to Ace' });
    expect(button.className).not.toMatch(/text-body!/);
  });

  it('shows the note of the model’s whole-item write in History', async () => {
    renderLongLabel();
    expect(await screen.findByText('Muse Spark Open wrote the item')).toBeInTheDocument();
    expect(screen.queryByText(/from nothing to nothing/)).toBeNull();
  });
});
