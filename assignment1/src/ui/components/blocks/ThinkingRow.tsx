import { Brain } from 'lucide-react';

export function ThinkingRow({ live }: { live: boolean }) {
  return (
    <div className={`thinking-row${live ? ' thinking-live' : ''}`} data-testid="thinking-row">
      <Brain size={12} /> <span>{live ? 'Thinking…' : 'Thought'}</span>
    </div>
  );
}
