export function formatDuration(ms: number | undefined): string {
  if (ms === undefined || !Number.isFinite(ms)) return '–';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) {
    const secText = s < 10 ? s.toFixed(1) : String(Math.round(s));
    // The rounded seconds text can itself reach '60' (e.g. 59999ms), which is not
    // a valid seconds display — fall through to the minutes branch instead.
    if (secText !== '60') return `${secText} s`;
  }
  const totalSeconds = Math.round(s);
  const m = Math.floor(totalSeconds / 60);
  const rest = totalSeconds - m * 60;
  return `${m} min ${rest} s`;
}

export function formatUsd(n: number | undefined): string {
  if (n === undefined) return '–';
  // Four decimals exist to keep a fraction of a cent from reading as free; exactly zero is not a
  // fraction of anything, and "$0.0000" made a run that never started look like a rounding error.
  if (n === 0) return '$0.00';
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

/**
 * The same duration for a narrow cell. The compare strip's stat cells are about 80px wide, where
 * "1 min 20 s" wraps onto two lines and "1m 20s" does not.
 */
export function formatDurationCompact(ms: number | undefined): string {
  if (ms === undefined || !Number.isFinite(ms)) return '–';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) {
    const secText = s < 10 ? s.toFixed(1) : String(Math.round(s));
    if (secText !== '60') return `${secText}s`;
  }
  const totalSeconds = Math.round(s);
  const m = Math.floor(totalSeconds / 60);
  return `${m}m ${totalSeconds - m * 60}s`;
}

export function formatTokens(n: number | undefined): string {
  if (n === undefined) return '–';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/**
 * `<n> <noun>`, with the noun in the right number. The footer read "1 turns" on every run that
 * ended in a single turn — which is every cancelled run, so it was in the demo screenshot.
 */
export function formatCount(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function shortId(id: string | undefined, n = 8): string {
  return id ? `${id.slice(0, n)}…` : '–';
}

/**
 * A file size. Decimal units, because that is what a file manager shows and the number here sits
 * next to a file name, not next to a memory figure.
 */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '–';
  if (n < 1000) return `${n} B`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)} kB`;
  if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(1)} MB`;
  return `${(n / 1_000_000_000).toFixed(2)} GB`;
}

/** How long ago, for a list where the exact minute does not matter. */
export function formatAgo(ts: number, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - ts) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function formatClock(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
