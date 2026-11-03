// Bottom sheets (vaul): filters, search, Play this moment, More actions, send confirmations.
// 20 px top radius, a handle, the one soft shadow, and safe-area padding at the bottom.
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
import { Drawer } from 'vaul';
import { cx } from '../lib/cx';
import { IconButton } from './Button';

export interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Always set: it names the dialog. Hide it visually with `hideTitle`. */
  title: string;
  hideTitle?: boolean;
  /** Something before the title in the header (e.g. a tape tab). */
  leading?: ReactNode;
  /** Shows a close button in the header. */
  closable?: boolean;
  /** `night` is the dark Play this moment sheet. */
  tone?: 'light' | 'night';
  /** Pinned below the scrolling body ("Show 3 items"). */
  footer?: ReactNode;
  children?: ReactNode;
  className?: string;
  /** Render inline instead of in a portal (the component gallery). */
  container?: HTMLElement | null;
}

export function Sheet({
  open,
  onOpenChange,
  title,
  hideTitle,
  leading,
  closable = false,
  tone = 'light',
  footer,
  children,
  className,
  container,
}: SheetProps) {
  const night = tone === 'night';
  return (
    <Drawer.Root open={open} onOpenChange={onOpenChange}>
      <Drawer.Portal container={container}>
        <Drawer.Overlay className="fixed inset-0 z-40 bg-night/45" />
        <Drawer.Content
          aria-describedby={undefined}
          className={cx(
            'fixed inset-x-0 bottom-0 z-50 mx-auto flex max-h-[92dvh] w-full max-w-[var(--app-width)] flex-col',
            'rounded-t-sheet shadow-lift outline-none',
            night ? 'on-night bg-night text-night-text' : 'bg-sheet text-graphite',
            className,
          )}
        >
          <div className="flex justify-center pt-2 pb-1" aria-hidden>
            <span className={cx('h-[5px] w-9 rounded-full', night ? 'bg-night-line' : 'bg-hairline')} />
          </div>
          <div className={cx('flex items-center gap-3 px-4', hideTitle && !closable ? 'sr-only' : 'min-h-12 pb-2')}>
            {leading}
            <Drawer.Title
              className={cx('min-w-0 flex-1 truncate text-card font-semibold', hideTitle && 'sr-only')}
            >
              {title}
            </Drawer.Title>
            {closable && (
              <Drawer.Close asChild>
                <IconButton label="Close" tone={night ? 'night' : 'track'} className="-mr-1 ml-auto">
                  <X size={20} strokeWidth={2.25} aria-hidden />
                </IconButton>
              </Drawer.Close>
            )}
          </div>
          <div className={cx('min-h-0 flex-1 overflow-y-auto overscroll-contain', !footer && 'pb-safe-3')}>
            {children}
          </div>
          {footer && (
            <div className={cx('border-t px-4 pt-3 pb-safe-3', night ? 'border-night-line' : 'border-hairline')}>
              {footer}
            </div>
          )}
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
}

/** A row in a More sheet: one action with its words. */
export function SheetAction({
  children,
  onClick,
  icon,
  tone = 'default',
}: {
  children: ReactNode;
  onClick: () => void;
  icon?: ReactNode;
  tone?: 'default' | 'danger';
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        'flex min-h-13 w-full items-center gap-3 border-b border-hairline px-4 text-left text-body last:border-b-0 active:bg-primer',
        tone === 'danger' ? 'text-safety' : 'text-graphite',
      )}
    >
      {icon && <span className="text-pencil">{icon}</span>}
      {children}
    </button>
  );
}
