// Toasts: "Sent 3 items to Ace Electric". One at a time, above the tab bar, announced politely.
import { CircleCheck, CircleAlert } from 'lucide-react';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { cx } from '../lib/cx';

export type ToastTone = 'success' | 'error' | 'info';

interface ToastMsg {
  id: number;
  message: string;
  tone: ToastTone;
  action?: { label: string; onClick: () => void };
}

interface ToastApi {
  show: (message: string, opts?: { tone?: ToastTone; action?: ToastMsg['action']; durationMs?: number }) => void;
  dismiss: () => void;
}

const Ctx = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastMsg | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const seq = useRef(0);

  const dismiss = useCallback(() => {
    clearTimeout(timer.current);
    setToast(null);
  }, []);

  const show = useCallback<ToastApi['show']>((message, opts = {}) => {
    clearTimeout(timer.current);
    seq.current += 1;
    setToast({ id: seq.current, message, tone: opts.tone ?? 'success', action: opts.action });
    timer.current = setTimeout(() => setToast(null), opts.durationMs ?? 3500);
  }, []);

  useEffect(() => () => clearTimeout(timer.current), []);

  const api = useMemo(() => ({ show, dismiss }), [show, dismiss]);
  return (
    <Ctx.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 z-[60] mx-auto max-w-[var(--app-width)] px-4"
        style={{ bottom: 'calc(var(--tabbar-h) + var(--sab) + 12px)' }}
      >
        {toast && <ToastView key={toast.id} toast={toast} onDismiss={dismiss} />}
      </div>
    </Ctx.Provider>
  );
}

export function ToastView({ toast, onDismiss }: { toast: Omit<ToastMsg, 'id'>; onDismiss?: () => void }) {
  const Icon = toast.tone === 'error' ? CircleAlert : CircleCheck;
  return (
    <div
      role={toast.tone === 'error' ? 'alert' : 'status'}
      className="on-night pointer-events-auto flex min-h-12 animate-slide-up items-center gap-3 rounded-button bg-night px-4 py-3 text-body text-night-text"
    >
      {toast.tone !== 'info' && (
        <Icon size={20} className={cx('shrink-0', toast.tone === 'error' ? 'text-hold' : 'text-tape-tab')} aria-hidden />
      )}
      <span className="flex-1">{toast.message}</span>
      {toast.action && (
        <button
          type="button"
          className="hit-44 shrink-0 font-semibold text-tape-tab"
          onClick={() => {
            toast.action?.onClick();
            onDismiss?.();
          }}
        >
          {toast.action.label}
        </button>
      )}
    </div>
  );
}

export function useToast(): ToastApi {
  const v = useContext(Ctx);
  if (!v) throw new Error('useToast needs <ToastProvider>');
  return v;
}
