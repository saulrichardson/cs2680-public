import {
  Bot,
  FileText,
  Globe,
  type LucideIcon,
  PenLine,
  Search,
  Sparkles,
  Terminal,
  Wrench,
} from 'lucide-react';
import { toolDisplayName } from '@/core/classify';
import type { ToolClass } from '@/core/types';

const ICONS: Record<string, LucideIcon> = {
  Read: FileText,
  Glob: Search,
  Grep: Search,
  LS: Search,
  Edit: PenLine,
  MultiEdit: PenLine,
  Write: PenLine,
  NotebookEdit: PenLine,
  Bash: Terminal,
  Agent: Bot,
  Task: Bot,
  WebFetch: Globe,
  WebSearch: Globe,
  Skill: Sparkles,
};

function toolIcon(name: string): LucideIcon {
  return ICONS[name] ?? Wrench;
}

export function ToolChip({
  name,
  toolClass,
  compact = false,
}: {
  name: string;
  toolClass: ToolClass;
  compact?: boolean;
}) {
  const Icon = toolIcon(name);
  // `title` keeps the exact wire name; the visible label drops an MCP server prefix that would
  // otherwise fill the chip and hide the action.
  return (
    <span className={`tool-chip cls-${toolClass}${compact ? ' tool-chip-compact' : ''}`} title={name}>
      <span className="tool-chip-icon">
        <Icon size={compact ? 12 : 13} strokeWidth={2.2} />
      </span>
      {!compact && <span className="tool-chip-name">{toolDisplayName(name)}</span>}
    </span>
  );
}
