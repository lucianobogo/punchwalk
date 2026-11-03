// Status and severity pills. Color is never the only signal: every pill carries its word.
import { Check, TriangleAlert } from 'lucide-react';
import type { ReactNode } from 'react';
import type { ItemStatus, Severity } from '../api/types';
import { SEVERITY_LABEL, UI_STATUS_LABEL, uiStatus } from '../lib/labels';
import { cx } from '../lib/cx';

export type PillTone = 'safety' | 'hold' | 'signed' | 'open' | 'neutral' | 'night';

const TONE: Record<PillTone, string> = {
  safety: 'bg-safety-bg text-safety',
  hold: 'bg-hold-card text-hold-ink border border-hold-border',
  signed: 'bg-sheet text-signed-ink border border-hairline',
  open: 'bg-tape-tint text-tape-ink',
  neutral: 'bg-track text-graphite',
  night: 'bg-night-raised text-night-text',
};

export function Pill({
  tone = 'neutral',
  icon,
  children,
  className,
}: {
  tone?: PillTone;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cx(
        'inline-flex h-6 shrink-0 items-center gap-1 rounded-full px-2.5 text-secondary font-medium whitespace-nowrap',
        TONE[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

/** Only Safety gets a pill in rows (DESIGN.md); the others show in Details. */
export function SeverityPill({ severity, className }: { severity: Severity; className?: string }) {
  if (severity === 'safety') {
    return (
      <Pill tone="safety" icon={<TriangleAlert size={14} strokeWidth={2.25} aria-hidden />} className={className}>
        {SEVERITY_LABEL.safety}
      </Pill>
    );
  }
  return (
    <Pill tone="neutral" className={className}>
      {SEVERITY_LABEL[severity]}
    </Pill>
  );
}

export function StatusPill({ status, className }: { status: ItemStatus; className?: string }) {
  const ui = uiStatus(status);
  if (ui === 'needs_look') {
    return (
      <Pill tone="hold" icon={<StatusDot />} className={className}>
        {UI_STATUS_LABEL.needs_look}
      </Pill>
    );
  }
  if (ui === 'signed_off') {
    return (
      <Pill
        tone="signed"
        icon={
          <span className="inline-flex size-3.5 items-center justify-center rounded-full bg-signed text-white">
            <Check size={10} strokeWidth={3.5} aria-hidden />
          </span>
        }
        className={className}
      >
        {UI_STATUS_LABEL.signed_off}
      </Pill>
    );
  }
  return (
    <Pill tone="open" className={className}>
      {UI_STATUS_LABEL.open}
    </Pill>
  );
}

/** The Hold dot that marks "Needs a look". */
export function StatusDot({ className }: { className?: string }) {
  return <span className={cx('inline-block size-2 shrink-0 rounded-full bg-hold', className)} aria-hidden />;
}
