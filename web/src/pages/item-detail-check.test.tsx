// Item detail's "Checked" section (DESIGN.md "Item detail", step 5). The verifier checks the original
// evidence. On a signed-off item the before/after proof is the story, so a stale "doesn't match" note
// from before the re-walk must not sit next to "Signed off". Every other status keeps showing it.
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createMemoryRouter } from 'react-router';
import { App } from '../App';
import type { Api } from '../api/api';
import { setApi } from '../api/client';
import { makeQueryClient } from '../api/hooks';
import { REVIEW_NOT_IN_FRAME, type ItemOut, type ItemStatus, type VerificationOut } from '../api/types';
import { showsCheck } from '../features/item/actions';
import { routes } from '../router';
import { diskMock } from '../test/fixtures';

/** Item 5 (signed off on the re-walk) with its original check turned inconsistent, as in the demo data. */
function withStaleCheck(item: ItemOut, status: ItemStatus = item.status): ItemOut {
  return {
    ...item,
    status,
    review_reason: status === 'needs_review' ? REVIEW_NOT_IN_FRAME : item.review_reason,
    verification: { ...item.verification!, consistent: false, note: REVIEW_NOT_IN_FRAME, issues: [] },
  };
}

function renderItem(status?: ItemStatus) {
  const { api } = diskMock();
  setApi({ ...api, item: async (id: string) => withStaleCheck(await api.item(id), status) } as Api);
  const router = createMemoryRouter(routes, { initialEntries: ['/items/itm_2b_05'] });
  return render(<App router={router} client={makeQueryClient()} />);
}

describe('item detail: Checked', () => {
  it('leaves out a stale "doesn’t match" note on a signed-off item', async () => {
    renderItem();
    expect(await screen.findByRole('heading', { name: 'Toothpaste splatter on mirror' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reopen' })).toBeInTheDocument();
    expect(screen.queryByText(REVIEW_NOT_IN_FRAME)).toBeNull();
    expect(screen.queryByText('Checked')).toBeNull();
  });

  it('still shows it on a needs-a-look item, next to the Needs a look reason', async () => {
    renderItem('needs_review');
    expect(await screen.findByRole('heading', { name: 'Toothpaste splatter on mirror' })).toBeInTheDocument();
    expect(screen.getAllByText('Needs a look').length).toBeGreaterThan(0);
    expect(screen.getByText('Checked')).toBeInTheDocument();
    // Once in the Needs a look card (review_reason), once in Checked (the verifier's note).
    expect(screen.getAllByText(REVIEW_NOT_IN_FRAME)).toHaveLength(2);
  });

  it.each([
    ['open', true],
    ['dispatched', true],
    ['needs_review', true],
    ['closed', false],
  ] as const)('%s + inconsistent check -> shown: %s', (status, shown) => {
    const verification: VerificationOut = { consistent: false, note: REVIEW_NOT_IN_FRAME, issues: [], provider: 'baseten', checked_at: null };
    expect(showsCheck({ status, verification })).toBe(shown);
  });

  it('keeps a consistent check, or no check yet, on a signed-off item', () => {
    const verification: VerificationOut = { consistent: true, note: null, issues: [], provider: 'baseten', checked_at: null };
    expect(showsCheck({ status: 'closed', verification })).toBe(true);
    expect(showsCheck({ status: 'closed', verification: null })).toBe(true);
  });
});
