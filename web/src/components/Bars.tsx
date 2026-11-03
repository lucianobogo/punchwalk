// Top bars, the bottom tab bar and the bottom action bar. All respect safe-area insets.
import { ChevronDown, ChevronLeft, HardHat, ListChecks, Video } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router';
import { cx } from '../lib/cx';

// ------------------------------------------------------------------ top bars

/**
 * Top-level screens: a large title (28 px, Expanded) that collapses into a compact bar on scroll.
 * `onTitleClick` adds a chevron (the punch list's project switcher).
 */
export function LargeTitleBar({
  title,
  onTitleClick,
  titleLabel,
  actions,
  subtitle,
  fixed = true,
}: {
  title: string;
  onTitleClick?: () => void;
  /** Accessible name for the title button, e.g. "Switch project, Unit 2B final punch". */
  titleLabel?: string;
  actions?: ReactNode;
  subtitle?: ReactNode;
  /** false renders the compact bar in place (gallery). */
  fixed?: boolean;
}) {
  const sentinel = useRef<HTMLDivElement>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const el = sentinel.current;
    if (!el || !fixed) return;
    const io = new IntersectionObserver(([e]) => setCollapsed(!e.isIntersecting), { threshold: 0 });
    io.observe(el);
    return () => io.disconnect();
  }, [fixed]);

  const heading = <h1 className="min-w-0 text-display font-semibold font-expanded text-graphite">{title}</h1>;

  return (
    <>
      {fixed && (
        <div
          className={cx(
            'fixed inset-x-0 top-0 z-30 mx-auto max-w-[var(--app-width)] border-b border-hairline bg-sheet pt-safe',
            'transition-opacity duration-150 ease-out',
            collapsed ? 'opacity-100' : 'pointer-events-none opacity-0',
          )}
          aria-hidden={!collapsed}
        >
          <div className="flex h-11 items-center justify-center px-14">
            <span className="truncate text-row font-semibold">{title}</span>
          </div>
        </div>
      )}
      <header className="px-4 pt-safe">
        {actions ? (
          <div className="flex min-h-11 items-center justify-end gap-1 pt-2">{actions}</div>
        ) : (
          <div className="h-5" aria-hidden />
        )}
        <div ref={sentinel} className="flex items-center gap-1 pb-1">
          {onTitleClick ? (
            <button
              type="button"
              onClick={onTitleClick}
              aria-label={titleLabel}
              aria-haspopup="dialog"
              className="flex min-w-0 items-center gap-2 text-left"
            >
              {heading}
              <ChevronDown size={24} strokeWidth={2} className="mt-1 shrink-0 text-graphite" aria-hidden />
            </button>
          ) : (
            heading
          )}
        </div>
        {subtitle && <p className="text-secondary text-pencil">{subtitle}</p>}
      </header>
    </>
  );
}

/** "< Back" in Tape ink, 44 px tall. */
export function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className="-ml-2 inline-flex min-h-11 items-center gap-0.5 pr-2 text-card text-tape-ink">
      <ChevronLeft size={26} strokeWidth={2.25} aria-hidden />
      <span>{label}</span>
    </Link>
  );
}

/**
 * Pushed screens: a sticky row with "< Back" and actions, then a 22 px Expanded title
 * with optional meta on the right ("Nov 3, 12 items") and a subtitle line.
 */
export function PushedBar({
  backTo,
  backLabel,
  title,
  meta,
  subtitle,
  actions,
  sticky = true,
}: {
  backTo: string;
  backLabel: string;
  title?: ReactNode;
  meta?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  sticky?: boolean;
}) {
  const scrolled = useScrolled(8);
  return (
    <>
      <div
        className={cx(
          'z-30 border-b bg-primer px-4 pt-safe transition-colors duration-150',
          sticky && 'sticky top-0',
          sticky && scrolled ? 'border-hairline' : 'border-transparent',
        )}
      >
        <div className="flex min-h-12 items-center gap-2 pt-1">
          <BackLink to={backTo} label={backLabel} />
          <div className="ml-auto flex items-center gap-1">{actions}</div>
        </div>
      </div>
      {(title || meta) && (
        <div className="px-4 pt-1">
          <div className="flex items-baseline gap-3">
            {title && <h1 className="min-w-0 flex-1 text-title font-semibold font-expanded-title text-graphite">{title}</h1>}
            {meta && <span className="ml-auto shrink-0 text-body tabular-nums text-pencil">{meta}</span>}
          </div>
          {subtitle && <p className="mt-1 text-secondary text-pencil">{subtitle}</p>}
        </div>
      )}
    </>
  );
}

function useScrolled(threshold: number) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > threshold);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [threshold]);
  return scrolled;
}

// ------------------------------------------------------------------ tab bar

/** Ring + dot, white, inside the Record button. */
function RecordGlyph() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
      <circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" strokeWidth="2.25" />
      <circle cx="10" cy="10" r="4" fill="currentColor" />
    </svg>
  );
}

const TABS = [
  { to: '/', label: 'Punch list', icon: ListChecks, match: (p: string) => p === '/' || p.startsWith('/items') },
  { to: '/walks', label: 'Walks', icon: Video, match: (p: string) => p.startsWith('/walks') },
  { to: '/record', label: 'Record', icon: null, match: (p: string) => p.startsWith('/record') },
  { to: '/trades', label: 'Trades', icon: HardHat, match: (p: string) => p.startsWith('/trades') },
] as const;

/** Bottom tab bar: four labeled tabs; Record is a filled Tape-blue button. White at 94%. */
export function TabBar({ fixed = true, activePath }: { fixed?: boolean; activePath?: string }) {
  const location = useLocation();
  const path = activePath ?? location.pathname;
  return (
    <nav
      aria-label="Main"
      className={cx(
        'z-30 border-t border-hairline bar-translucent pb-safe',
        fixed && 'fixed inset-x-0 bottom-0 mx-auto max-w-[var(--app-width)]',
      )}
    >
      <ul className="grid h-[var(--tabbar-h)] grid-cols-4">
        {TABS.map((t) => {
          const active = t.match(path);
          const Icon = t.icon;
          return (
            <li key={t.to} className="flex">
              <NavLink
                to={t.to}
                aria-current={active ? 'page' : undefined}
                className={cx(
                  'flex flex-1 flex-col items-center justify-center gap-1 text-tab',
                  active ? 'font-semibold text-tape-ink' : 'font-medium text-pencil',
                )}
              >
                {Icon ? (
                  <Icon size={26} strokeWidth={1.75} aria-hidden />
                ) : (
                  <span
                    className={cx(
                      'flex h-7 w-11 items-center justify-center rounded-control text-white',
                      active ? 'bg-tape-ink' : 'bg-tape',
                    )}
                  >
                    <RecordGlyph />
                  </span>
                )}
                <span className={cx(!Icon && !active && 'font-semibold text-graphite')}>{t.label}</span>
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

// ------------------------------------------------------------------ bottom action bar

/** Item detail's bottom bar: one primary action plus More. White at 94%, safe-area padded. */
export function ActionBar({ children, fixed = true, className }: { children: ReactNode; fixed?: boolean; className?: string }) {
  return (
    <div
      className={cx(
        'z-30 flex items-center gap-3 border-t border-hairline bar-translucent px-4 pt-3 pb-safe-3',
        fixed && 'fixed inset-x-0 bottom-0 mx-auto max-w-[var(--app-width)]',
        className,
      )}
    >
      {children}
    </div>
  );
}
