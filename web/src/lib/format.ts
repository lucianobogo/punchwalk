// Times, dates and money, always in plain words. Dates use the viewer's time zone; tests pin it
// with setDisplayTimeZone so their expectations don't depend on the machine.

let displayTimeZone: string | undefined;

export function setDisplayTimeZone(tz: string | undefined) {
  displayTimeZone = tz;
}

/** 14.2 -> "0:14", 92 -> "1:32", 3725 -> "1:02:05". */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

/** 112000 -> "1 min 52 s", 41000 -> "41 s", 2300 -> "2.3 s". */
export function formatDuration(ms: number): string {
  if (ms < 10_000) return `${Math.round(ms / 100) / 10} s`;
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m === 0) return `${s} s`;
  return s === 0 ? `${m} min` : `${m} min ${s} s`;
}

/** 0.4312 -> "$0.43"; tiny amounts keep one significant digit ("$0.004"). */
export function formatCost(usd: number): string {
  if (usd > 0 && usd < 0.01) return `$${usd.toPrecision(1)}`;
  return `$${usd.toFixed(2)}`;
}

/** "Nov 3" */
export function formatShortDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: displayTimeZone,
  });
}

/** "8:05 AM" */
export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: displayTimeZone,
  });
}

/** "Nov 4 at 8:05 AM" */
export function formatDateTime(iso: string): string {
  return `${formatShortDate(iso)} at ${formatTime(iso)}`;
}
