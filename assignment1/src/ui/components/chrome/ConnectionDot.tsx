'use client';
import { useMissionStore } from '@/ui/store/missionStore';

export function ConnectionDot() {
  const connection = useMissionStore((s) => s.connection);
  const label =
    connection === 'open' ? 'Connected' : connection === 'connecting' ? 'Connecting…' : 'Reconnecting…';
  return (
    <span
      className={`conn conn-${connection}`}
      title={label}
      data-testid="connection"
      data-state={connection}
    >
      <span className="conn-dot" />
      <span className="sr-only">{label}</span>
    </span>
  );
}
