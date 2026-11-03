import type { ButtonHTMLAttributes, ComponentProps, ReactNode } from 'react';
import { Link } from 'react-router';
import { cx } from '../lib/cx';

export type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'night' | 'night-quiet' | 'night-link';
export type ButtonSize = 'md' | 'lg';

const VARIANT: Record<ButtonVariant, string> = {
  // Tape ink with white text passes AA (DESIGN.md "Color").
  primary: 'bg-tape-ink text-white active:brightness-90 disabled:opacity-50',
  secondary: 'bg-sheet text-graphite border border-hairline active:bg-primer disabled:text-pencil',
  quiet: 'text-tape-ink active:opacity-70 disabled:text-pencil',
  night: 'bg-sheet text-graphite active:brightness-90',
  'night-quiet': 'text-night-text active:opacity-70',
  'night-link': 'text-tape-tab active:opacity-70 disabled:text-night-pencil',
};

const SIZE: Record<ButtonSize, string> = {
  md: 'min-h-11 px-4 text-body font-semibold',
  lg: 'min-h-12 px-5 text-card font-semibold',
};

export function buttonClass(variant: ButtonVariant = 'primary', size: ButtonSize = 'md', block = false) {
  return cx(
    'inline-flex items-center justify-center gap-2 rounded-button text-center select-none',
    'transition-[filter,background-color,opacity] duration-150 ease-out disabled:cursor-not-allowed',
    VARIANT[variant],
    SIZE[size],
    (variant.includes('quiet') || variant === 'night-link') && 'px-2',
    block && 'w-full',
  );
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  block?: boolean;
  icon?: ReactNode;
  /** While busy the label says what is happening ("Sending..."), never a bare spinner. */
  busy?: boolean;
  busyLabel?: string;
}

export function Button({
  variant = 'primary',
  size = 'md',
  block,
  icon,
  busy,
  busyLabel,
  className,
  children,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(buttonClass(variant, size, block), className)}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {icon}
      {busy && busyLabel ? busyLabel : children}
    </button>
  );
}

export interface ButtonLinkProps extends ComponentProps<typeof Link> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  block?: boolean;
}

export function ButtonLink({ variant = 'primary', size = 'md', block, className, ...rest }: ButtonLinkProps) {
  return <Link className={cx(buttonClass(variant, size, block), className)} {...rest} />;
}

export type IconButtonTone = 'plain' | 'track' | 'night' | 'bordered';

const ICON_TONE: Record<IconButtonTone, string> = {
  plain: 'text-graphite active:bg-track',
  track: 'bg-track text-graphite active:brightness-95',
  night: 'bg-night-raised text-night-text active:brightness-125',
  bordered: 'bg-sheet border border-hairline text-graphite active:bg-primer',
};

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: icon buttons have no visible words. */
  label: string;
  tone?: IconButtonTone;
  /** Visual diameter; the hit area is always at least 44 px. */
  size?: 44 | 48;
  square?: boolean;
}

export function IconButton({
  label,
  tone = 'plain',
  size = 44,
  square = false,
  className,
  children,
  type = 'button',
  ...rest
}: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={cx(
        'inline-flex shrink-0 items-center justify-center transition-[filter,background-color] duration-150',
        square ? 'rounded-button' : 'rounded-full',
        size === 44 ? 'size-11' : 'size-12',
        ICON_TONE[tone],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
