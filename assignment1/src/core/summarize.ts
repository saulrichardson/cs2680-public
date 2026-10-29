export function shortenPath(path: string, cwd?: string, home?: string): string {
  if (cwd && (path === cwd || path.startsWith(`${cwd}/`)))
    return path === cwd ? '.' : path.slice(cwd.length + 1);
  if (home && path.startsWith(`${home}/`)) return `~${path.slice(home.length)}`;
  return path;
}

/**
 * The first non-blank line of a blob, truncated. Used by `summarizeInput` below to turn a command
 * or a subagent prompt into one line of card header.
 */
function firstLine(s: string, max = 120): string {
  const line =
    s
      .split('\n')
      .map((l) => l.trim())
      .find((l) => l.length > 0) ?? '';
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined;
}

function lineCount(s: string): number {
  return s.length === 0 ? 0 : s.split('\n').length;
}

export interface InputSummary {
  primary: string;
  secondary?: string;
}

export function summarizeInput(name: string, input: Record<string, unknown>, cwd?: string): InputSummary {
  switch (name) {
    case 'Read': {
      const p = str(input.file_path);
      return { primary: p ? shortenPath(p, cwd) : '(no path)' };
    }
    case 'Edit':
    case 'MultiEdit': {
      const p = str(input.file_path);
      const oldS = str(input.old_string) ?? '';
      const newS = str(input.new_string) ?? '';
      const secondary = oldS || newS ? `-${lineCount(oldS)} +${lineCount(newS)} lines` : undefined;
      return secondary
        ? { primary: p ? shortenPath(p, cwd) : '(no path)', secondary }
        : { primary: p ? shortenPath(p, cwd) : '(no path)' };
    }
    case 'Write':
    case 'NotebookEdit': {
      const p = str(input.file_path) ?? str(input.notebook_path);
      const content = str(input.content) ?? str(input.new_source);
      const secondary = content ? `${lineCount(content)} lines` : undefined;
      return secondary
        ? { primary: p ? shortenPath(p, cwd) : '(no path)', secondary }
        : { primary: p ? shortenPath(p, cwd) : '(no path)' };
    }
    case 'Bash': {
      const cmd = str(input.command) ?? '';
      const desc = str(input.description);
      return desc ? { primary: firstLine(cmd), secondary: desc } : { primary: firstLine(cmd) };
    }
    case 'Grep': {
      const pattern = str(input.pattern) ?? '';
      const p = str(input.path);
      return p
        ? { primary: `"${pattern}"`, secondary: `in ${shortenPath(p, cwd)}` }
        : { primary: `"${pattern}"` };
    }
    case 'Glob':
    case 'LS': {
      const pattern = str(input.pattern) ?? str(input.path) ?? '';
      return { primary: pattern ? shortenPath(pattern, cwd) : '(no pattern)' };
    }
    case 'Agent':
    case 'Task': {
      const desc = str(input.description);
      const prompt = str(input.prompt);
      const type = str(input.subagent_type);
      const primary = desc ?? (prompt ? firstLine(prompt) : '(subagent)');
      return type ? { primary, secondary: type } : { primary };
    }
    case 'WebFetch':
    case 'WebSearch': {
      const primary = str(input.url) ?? str(input.query) ?? '';
      return { primary };
    }
    case 'Skill': {
      return { primary: str(input.skill) ?? str(input.name) ?? '(skill)' };
    }
    default: {
      const json = JSON.stringify(input);
      return { primary: json.length > 120 ? `${json.slice(0, 119)}…` : json };
    }
  }
}
