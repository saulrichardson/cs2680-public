'use client';
import type { inferRouterOutputs } from '@trpc/server';
import { ChevronRight, Download, File, Folder, Link2Off, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import { previewSandboxFor } from '@/core/preview';
import { workspaceDir, workspaceFileUrl } from '@/core/workspaceLinks';
import type { AppRouter } from '@/server/trpc/router';
import { TextBlock } from '@/ui/components/blocks/TextBlock';
import { formatBytes, formatClock } from '@/ui/format';
import { useMissionStore } from '@/ui/store/missionStore';
import { trpc } from '@/ui/trpc/client';

/**
 * What the session actually produced, opened from the session.
 *
 * A run that reports "I wrote SURVEY.md and plan.svg" has produced nothing the operator can use
 * until they can read them, and asking them to go and find the worktree on disk is the console
 * admitting it stopped at the interesting part. So this lists the lane's own working directory and
 * renders what it finds.
 *
 * What it is not is a file server. Every path goes to the server as a lane id plus a relative path,
 * and the server resolves symlinks before deciding whether the result is inside that lane's
 * directory (`resolveInWorkspace`); nothing else on the machine is reachable through it.
 *
 * Generated markup is rendered in a frame with `sandbox="allow-scripts"` and no `allow-same-origin`,
 * against a response whose own CSP says the same: a self-contained plan's layer toggles work, and
 * the document has an opaque origin from which this console's pages and endpoints are unreachable.
 */
export function OutputsPanel({ laneId }: { laneId: string }) {
  // The pane can be opened *at* a file — that is how a link inside the agent's own handoff index
  // lands on the document it names. Local state takes over for ordinary browsing afterwards.
  const requested = useMissionStore((s) => {
    const pane = s.paneByLane[laneId];
    return pane?.kind === 'outputs' ? pane.path : undefined;
  });
  const [dir, setDir] = useState(() => (requested ? workspaceDir(requested) : ''));
  const [file, setFile] = useState<string | null>(requested ?? null);
  /**
   * Whether the file list is showing *at narrow widths*.
   *
   * At 390px the list, the team band and the composer left about 90px for the document, which is
   * not reading a document. Choosing a file therefore steps out of the list and into the preview,
   * the way a phone file browser does, and the Files control steps back. On a desktop the list is
   * always shown — the stylesheet ignores this flag above the breakpoint — so the two-pane layout
   * that works there is unaffected.
   */
  const [listOpen, setListOpen] = useState(!requested);
  const open = (path: string) => {
    setFile(path);
    setDir(workspaceDir(path));
    setListOpen(false);
  };
  // Keyed on the requested path alone: an ordinary re-render must not undo browsing the operator
  // has done since they followed the link. Both setters are stable, so nothing else belongs here.
  useEffect(() => {
    if (!requested) return;
    setFile(requested);
    setDir(workspaceDir(requested));
    setListOpen(false);
  }, [requested]);
  const listing = trpc.outputs.list.useQuery({ laneId, path: dir });
  /**
   * The selected document, read here rather than inside `Preview`, because Refresh has to reach it.
   *
   * It used to be `Preview`'s own query, and Refresh only refetched the listing: the list reported
   * the file's new size while the pane beside it still showed the old text, and clicking the same
   * name again changed no query key and so fetched nothing. The pane could therefore contradict
   * itself immediately after the agent rewrote an output — the one moment it exists for.
   */
  const selected = trpc.outputs.read.useQuery({ laneId, path: file ?? '' }, { enabled: file !== null });
  /**
   * `version` is what an image or a framed document is refreshed by. Their bytes come from the raw
   * route, not from a query, and a browser re-requests those only when the URL changes.
   */
  const [version, setVersion] = useState(0);
  /** One Refresh, one owner: the listing, the open document, and the frame or image showing it. */
  const refresh = () => {
    void listing.refetch();
    if (file !== null) void selected.refetch();
    setVersion((v) => v + 1);
  };
  return (
    <section className="outputs" data-testid="outputs" data-list={listOpen ? 'open' : 'closed'}>
      {/* Only rendered by the stylesheet at narrow widths; on a desktop both panes are side by side
          and a control for revealing one of them would be a control that does nothing. */}
      <button
        type="button"
        className="outputs-files-toggle"
        aria-expanded={listOpen}
        onClick={() => setListOpen((o) => !o)}
        data-testid="outputs-files-toggle"
      >
        <Folder size={13} /> {listOpen ? 'Hide files' : 'Files'}
        {file && !listOpen && <span className="faint truncate"> · {file}</span>}
      </button>
      <div className="outputs-list">
        <header className="outputs-head">
          <Crumbs dir={dir} onGo={(next) => setDir(next)} />
          <span className="spacer" />
          <button
            type="button"
            className="btn btn-ghost"
            onClick={refresh}
            aria-label="Refresh the files and what is open"
            title="Refresh — the agent may still be writing"
            data-testid="outputs-refresh"
          >
            <RefreshCw size={13} />
          </button>
        </header>
        {listing.error && (
          <p className="error" role="alert" data-testid="outputs-error">
            {listing.error.message}
          </p>
        )}
        {listing.data?.entries.length === 0 && (
          <p className="faint" data-testid="outputs-empty">
            Nothing here yet.
          </p>
        )}
        <ul className="outputs-entries">
          {listing.data?.entries.map((entry) => (
            <li key={entry.path}>
              <button
                type="button"
                className={`outputs-entry${file === entry.path ? ' selected' : ''}`}
                disabled={entry.blocked}
                title={
                  entry.blocked
                    ? 'This is a symbolic link pointing outside the session workspace, so it cannot be opened here.'
                    : entry.path
                }
                onClick={() => (entry.kind === 'directory' ? setDir(entry.path) : open(entry.path))}
                data-name={entry.name}
                data-testid="outputs-entry"
                data-kind={entry.kind}
                data-blocked={entry.blocked || undefined}
              >
                {entry.blocked ? (
                  <Link2Off size={13} />
                ) : entry.kind === 'directory' ? (
                  <Folder size={13} />
                ) : (
                  <File size={13} />
                )}
                <span className="truncate">{entry.name}</span>
                <span className="faint mono">
                  {entry.kind === 'directory' ? '' : formatBytes(entry.size)}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {listing.data?.truncated && (
          <p className="faint">Only the first entries are listed; this directory has more.</p>
        )}
      </div>
      <div className="outputs-preview" data-testid="outputs-preview-box">
        {file ? (
          <Preview
            laneId={laneId}
            path={file}
            query={selected}
            version={version}
            onOpen={open}
            onClose={() => setFile(null)}
          />
        ) : (
          <p className="faint outputs-hint">Choose a file to read it here.</p>
        )}
      </div>
    </section>
  );
}

function Crumbs({ dir, onGo }: { dir: string; onGo(next: string): void }) {
  const parts = dir === '' ? [] : dir.split('/');
  return (
    <nav className="outputs-crumbs" aria-label="Folder">
      <button type="button" className="outputs-crumb" onClick={() => onGo('')} data-testid="outputs-root">
        Workspace
      </button>
      {parts.map((part, i) => (
        <span key={parts.slice(0, i + 1).join('/')}>
          <ChevronRight size={11} aria-hidden="true" />
          <button
            type="button"
            className="outputs-crumb"
            onClick={() => onGo(parts.slice(0, i + 1).join('/'))}
          >
            {part}
          </button>
        </span>
      ))}
    </nav>
  );
}

/** What `outputs.read` answers with: the open file as the server described it. */
type OpenFile = inferRouterOutputs<AppRouter>['outputs']['read'];

/**
 * The open document. Its content comes from the panel, which owns Refresh; `version` is what makes
 * the raw route hand back the file again for the kinds the browser fetches for itself.
 */
function Preview({
  laneId,
  path,
  query: file,
  version,
  onOpen,
  onClose,
}: {
  laneId: string;
  path: string;
  query: { data: OpenFile | undefined; error: { message: string } | null };
  version: number;
  onOpen(next: string): void;
  onClose(): void;
}) {
  const raw = workspaceFileUrl(laneId, path, { version });
  if (file.error)
    return (
      <p className="error" role="alert" data-testid="preview-error">
        {file.error.message}
      </p>
    );
  const data = file.data;
  if (!data) return <p className="faint">Reading…</p>;
  return (
    <div className="preview" data-testid="preview" data-preview={data.preview}>
      <header className="preview-head">
        <span className="mono truncate" title={data.path} data-testid="preview-path">
          {data.path}
        </span>
        <span className="faint mono">
          {formatBytes(data.size)} · {formatClock(data.modifiedAt)}
        </span>
        <span className="spacer" />
        <a
          className="btn btn-ghost"
          href={workspaceFileUrl(laneId, path, { download: true })}
          download
          data-testid="preview-download"
        >
          <Download size={13} /> Download
        </a>
        <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Close the preview">
          ×
        </button>
      </header>
      {data.truncated && (
        <p className="caution" data-testid="preview-truncated">
          Showing the first part of this file; download it for the rest.
        </p>
      )}
      {data.preview === 'markdown' && data.text !== undefined && (
        <div className="preview-markdown" data-testid="preview-markdown">
          {/* Links resolve against *this document's* directory, so a proposal in `handoff/` linking
              to `../designs/plan.svg` opens the drawing instead of a route that does not exist. */}
          <TextBlock markdown={data.text} links={{ laneId, cwd: '', dir: workspaceDir(path), onOpen }} />
        </div>
      )}
      {data.preview === 'text' && data.text !== undefined && (
        <pre className="preview-text mono" data-testid="preview-text">
          {data.text}
        </pre>
      )}
      {data.preview === 'image' && (
        // biome-ignore lint/performance/noImgElement: next/image optimizes build-time assets through the image pipeline; this is a file an agent wrote a moment ago at a path only known at runtime, and routing it through the optimizer would both fail and lose the sandboxed headers the workspace route sets.
        <img className="preview-image" src={raw} alt={data.name} data-testid="preview-image" />
      )}
      {data.preview === 'document' && (
        <>
          {/* `allow-scripts` *without* `allow-same-origin`: the document gets a unique opaque origin,
              so a generated plan's own layer toggles actually work while it can reach nothing of
              Mission Control's. Forms, popups and top-level navigation are simply not granted, and
              the response's own CSP says the same thing independently. */}
          <iframe
            className="preview-frame"
            title={`Preview of ${data.name}`}
            src={raw}
            sandbox={previewSandboxFor(data.preview)}
            data-testid="preview-frame"
          />
          <p className="faint">
            Interactive, in an isolated origin with no network access: the document's own scripts run, and it
            cannot reach this console or fetch anything remote.
          </p>
        </>
      )}
      {data.preview === 'none' && (
        <p className="faint" data-testid="preview-unsupported">
          This console does not render {data.name.split('.').pop()} files. Download it to open it.
        </p>
      )}
    </div>
  );
}
