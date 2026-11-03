import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TAPE_PATH, TapeTab } from './TapeTab';

describe('TapeTab', () => {
  it('shows the bare number: no "#" and no leading zeros', () => {
    render(<TapeTab number={7} />);
    const tab = screen.getByRole('img', { name: 'Item 7' });
    expect(tab).toHaveTextContent(/^7$/);
  });

  it('draws the ragged strip from the reviewed SVG path, not a CSS rectangle', () => {
    const { container } = render(<TapeTab number={12} />);
    expect(container.querySelector('path')?.getAttribute('d')).toBe(TAPE_PATH);
  });

  it('has the two sizes from DESIGN.md', () => {
    const { container, rerender } = render(<TapeTab number={3} size="lg" />);
    const el = () => container.firstElementChild as HTMLElement;
    expect([el().style.width, el().style.height]).toEqual(['40px', '24px']);
    rerender(<TapeTab number={3} size="sm" />);
    expect([el().style.width, el().style.height]).toEqual(['32px', '20px']);
  });

  it('says the status badge in words, not only color', () => {
    render(
      <>
        <TapeTab number={2} badge="hold" />
        <TapeTab number={1} badge="signed" />
      </>,
    );
    expect(screen.getByRole('img', { name: 'Item 2, needs a look' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Item 1, signed off' })).toBeInTheDocument();
  });

  it('stays out of the accessibility tree when decorative', () => {
    render(<TapeTab number={5} decorative />);
    expect(screen.queryByRole('img')).toBeNull();
  });
});
