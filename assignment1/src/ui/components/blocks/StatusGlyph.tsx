import { Check, Circle, X } from 'lucide-react';
import type { CallStatus } from '@/core/types';

export function StatusGlyph({ status, runEnded }: { status: CallStatus; runEnded: boolean }) {
  if (status === 'done')
    return (
      <span className="glyph glyph-done" role="img" aria-label="done" title="Completed">
        <Check size={13} strokeWidth={3} />
      </span>
    );
  if (status === 'error')
    return (
      <span className="glyph glyph-error" role="img" aria-label="error" title="Returned an error">
        <X size={13} strokeWidth={3} />
      </span>
    );
  if (runEnded)
    return (
      <span
        className="glyph glyph-unresolved"
        role="img"
        aria-label="cancelled"
        title="No result before the run ended"
      >
        <Circle size={10} />
      </span>
    );
  return (
    <span className="glyph glyph-pending" role="img" aria-label="pending" title="Waiting for the result">
      <span className="glyph-dot" />
    </span>
  );
}
