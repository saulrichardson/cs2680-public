'use client';
import { Folder } from 'lucide-react';
import Link from 'next/link';
import type { LaneDto } from '@/core/types';
import { AppChrome } from '@/ui/components/chrome/AppChrome';
import { SessionForm } from '@/ui/components/session/SessionForm';
import { formatAgo } from '@/ui/format';
import { projectHref, projectLabel } from '@/ui/project';
import { useLaneStatus } from '@/ui/store/selectors';
import { trpc } from '@/ui/trpc/client';

/**
 * Home: choose a folder and start, or go back to work already in progress.
 *
 * Two things only. The form is the same one a project shows, so there is one way to start a
 * session. The lists below are labelled by project, because the complaint that produced this
 * screen was seeing unrelated conversations presented as though they belonged together — here
 * they are deliberately global and deliberately say which project each one is.
 */
export function SessionStart() {
  return (
    <AppChrome>
      <div className="start" data-testid="session-start">
        <section className="start-card">
          <h1>Start a session</h1>
          <p className="muted">
            One prompt against one folder. Claude's own subagents appear as they are created, and you can open
            any of them without leaving the conversation.
          </p>
          <SessionForm />
        </section>
        <div className="start-side">
          <Projects />
          <RecentSessions />
        </div>
      </div>
    </AppChrome>
  );
}

/** The folders that have work in them, most recently active first. */
function Projects() {
  const projects = trpc.projects.list.useQuery();
  const rows = projects.data ?? [];
  if (rows.length === 0) return null;
  return (
    <section className="start-recent" data-testid="projects">
      <header>
        <h2>Projects</h2>
      </header>
      <ul>
        {rows.map((p) => (
          <li key={p.root}>
            {/* `projectHref`, never a hand-rolled URL: the route decodes base64url, so a
                percent-encoded path reaches it as an unreadable segment and the page throws. */}
            <Link
              href={projectHref(p.root)}
              className="start-recent-row"
              data-testid="project-row"
              data-project-root={p.root}
            >
              <Folder size={13} />
              <span className="truncate">{projectLabel(p.root)}</span>
              <span className="faint mono truncate">{p.root}</span>
              <span className="faint">{p.sessions === 1 ? '1 session' : `${p.sessions} sessions`}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Recent conversations across every project, each labelled with the project it belongs to. */
function RecentSessions() {
  const lanes = trpc.lanes.list.useQuery();
  const ordered = [...(lanes.data ?? [])].sort((a, b) => b.lastActivityAt - a.lastActivityAt).slice(0, 8);
  if (ordered.length === 0) return null;
  return (
    <section className="start-recent" data-testid="recent-sessions">
      <header>
        <h2>Recent</h2>
      </header>
      <ul>
        {ordered.map((lane) => (
          <li key={lane.id}>
            <RecentRow lane={lane} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function RecentRow({ lane }: { lane: LaneDto }) {
  const status = useLaneStatus(lane.id);
  return (
    <Link href={`/lanes/${lane.id}`} className="start-recent-row" data-testid="recent-session">
      <span className={`pill pill-${status === 'idle' ? 'cancelled' : status}`}>{status}</span>
      <span className="truncate">{lane.name}</span>
      <span className="faint mono truncate" data-testid="recent-session-project">
        {projectLabel(lane.projectRoot)}
      </span>
      <span className="faint">{formatAgo(lane.lastActivityAt)}</span>
    </Link>
  );
}
