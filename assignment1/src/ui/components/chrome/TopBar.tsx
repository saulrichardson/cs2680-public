'use client';
import { ChevronRight, Command, Moon, Sun, SunMoon } from 'lucide-react';
import Link from 'next/link';
import { projectHref, projectLabel } from '@/ui/project';
import { usePalette } from '@/ui/store/palette';
import { useRunningCount } from '@/ui/store/selectors';
import { useTheme } from '@/ui/theme/useTheme';
import { ConnectionDot } from './ConnectionDot';
import { useShortcutHints } from './useShortcutHints';

/**
 * Where you are, and the two controls that are not about the work in front of you.
 *
 * The segmented Start / Overview / Compare navigation is gone. Overview was a second global
 * history of every conversation on the machine, which is the thing that made unrelated projects
 * look related; Compare listed races, a capability that never belonged in primary navigation.
 * What replaces them is a trail — Mission Control › project › session — because that is the actual
 * structure of the work.
 */
export function TopBar({ project, session }: { project?: string | undefined; session?: string | undefined }) {
  const running = useRunningCount();
  const { theme, cycle } = useTheme();
  const hints = useShortcutHints();
  const nextTheme = theme === 'system' ? 'dark' : theme === 'dark' ? 'light' : 'system';
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : SunMoon;
  return (
    <header className="topbar" data-testid="topbar">
      <nav className="topbar-left" aria-label="Breadcrumb">
        {/* On a phone the brand compacts to its initials rather than pushing the actions off the
            edge. Both spellings are in the DOM and CSS chooses; the link keeps one accessible name
            either way, so a screen reader hears "Mission Control" at every width. */}
        <Link href="/" className="wordmark" aria-label="Mission Control">
          <span className="wordmark-full">Mission Control</span>
          <span className="wordmark-short" aria-hidden="true">
            MC
          </span>
        </Link>
        {project && (
          <>
            <ChevronRight size={13} className="faint" aria-hidden="true" />
            <Link href={projectHref(project)} className="crumb" data-testid="crumb-project" title={project}>
              {projectLabel(project)}
            </Link>
          </>
        )}
        {session && (
          <>
            <ChevronRight size={13} className="faint" aria-hidden="true" />
            <span className="crumb current truncate" data-testid="crumb-session">
              {session}
            </span>
          </>
        )}
      </nav>
      <div className="topbar-right">
        {running > 0 && (
          // Navigation, not a dashboard: it says how much is live and takes you to the list.
          <Link
            href="/"
            className="pill pill-running"
            data-testid="running-count"
            title={`${running} running`}
          >
            {running}
            <span className="pill-text"> running</span>
          </Link>
        )}
        <ConnectionDot />
        {/* The palette is the only way to reach Import, and a phone has no ⌘K to offer instead — so
            on a narrow screen the hint gives way to an icon and the button stays. */}
        <button
          type="button"
          className="btn btn-ghost faint"
          onClick={() => usePalette.getState().setOpen(true)}
          aria-label="Open command palette"
          title="Command palette"
          data-testid="palette-open"
        >
          <span className="palette-hint">{hints.palette}</span>
          <Command className="palette-icon" size={16} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={cycle}
          aria-label={`Theme: ${theme} — switch to ${nextTheme}`}
          title={`Theme: ${theme} — switch to ${nextTheme}`}
        >
          <ThemeIcon size={16} />
        </button>
      </div>
    </header>
  );
}
