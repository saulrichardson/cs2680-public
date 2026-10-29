'use client';
import { AlertTriangle, ChevronRight, File, Folder, Link2Off } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import {
  WRAPUP_MAX_FILE_BYTES,
  WRAPUP_MAX_FILES,
  WRAPUP_MAX_INSTRUCTIONS,
  WRAPUP_MAX_TOTAL_BYTES,
} from '@/core/wrapup';
import { useDialogFocus } from '@/ui/components/chrome/useDialogFocus';
import { formatBytes } from '@/ui/format';
import { useMissionStore } from '@/ui/store/missionStore';
import { trpc } from '@/ui/trpc/client';

/** One file the operator has chosen to carry in, kept with its size so the bounds can be shown. */
interface PickedFile {
  laneId: string;
  path: string;
  bytes: number;
}

/**
 * Bringing several of a project's sessions together into a new one.
 *
 * Everything consequential is visible before it happens: which sessions, how each ended, which
 * files would be copied, and what the new session is asked to do. The three refusals it shows —
 * still running, did not finish, changed since you chose it — are the server's own rules
 * (`planWrapUp`), surfaced early rather than reimplemented.
 */
export function BringTogetherDialog({
  projectRoot,
  laneIds,
  onClose,
}: {
  projectRoot: string;
  /** The workspace's columns, in order: what the package's sources will be. */
  laneIds: string[];
  onClose(): void;
}) {
  const dialog = useDialogFocus<HTMLDivElement>();
  const router = useRouter();
  const [instructions, setInstructions] = useState('');
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const sources = trpc.wrapups.sources.useQuery(
    { laneIds },
    {
      staleTime: 0,
      refetchOnMount: 'always',
      // While a source is active, keep the disabled dialog in step with its actual completion.
      refetchInterval: (query) =>
        query.state.data?.some((s) => s.busy || s.outcome === 'running') ? 1000 : false,
    },
  );
  const utils = trpc.useUtils();
  const create = trpc.wrapups.create.useMutation({
    onSuccess: (made) => {
      useMissionStore.getState().upsertLane(made.lane);
      void utils.lanes.list.invalidate();
      void utils.projects.list.invalidate();
      onClose();
      router.push(`/lanes/${made.lane.id}`);
    },
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && !create.isPending && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, create.isPending]);

  const rows = sources.data ?? [];
  const busy = rows.filter((s) => s.busy || s.outcome === 'running');
  const unfinished = rows.filter((s) => s.needsAcknowledgement && !(s.busy || s.outcome === 'running'));
  const totalBytes = files.reduce((n, f) => n + f.bytes, 0);
  const needsAck = unfinished.length > 0;
  const blocked =
    sources.isPending ||
    sources.isError ||
    rows.length === 0 ||
    busy.length > 0 ||
    (needsAck && !acknowledged) ||
    instructions.trim().length === 0 ||
    create.isPending;

  const toggleFile = (file: PickedFile) =>
    setFiles((current) =>
      current.some((f) => f.laneId === file.laneId && f.path === file.path)
        ? current.filter((f) => !(f.laneId === file.laneId && f.path === file.path))
        : [...current, file],
    );

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: scrim closes on click; Escape is handled above.
    <div className="scrim" onClick={() => !create.isPending && onClose()} role="presentation">
      <div
        className="dialog dialog-wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bring-together-title"
        ref={dialog.ref}
        onKeyDown={dialog.onKeyDown}
        onClick={(e) => e.stopPropagation()}
        data-testid="bring-together-dialog"
      >
        <h2 id="bring-together-title">Bring together</h2>
        <p className="muted">
          Start a new session with captured reports and selected files from these sessions. Its work is saved
          in a separate folder.
        </p>

        {sources.isPending && <p className="faint">Reading what these sessions have produced…</p>}
        {sources.error && (
          <p className="error" role="alert" data-testid="bring-together-error">
            {sources.error.message}
          </p>
        )}

        <ul className="bring-sources" data-testid="bring-sources">
          {rows.map((source, i) => {
            const running = source.busy || source.outcome === 'running';
            return (
              <li
                key={source.laneId}
                className="bring-source"
                data-testid="bring-source"
                data-lane={source.laneId}
              >
                <span className="bring-source-ordinal faint">{i + 1}</span>
                <span className="truncate">{source.name}</span>
                <span
                  className={`pill pill-${source.outcome === 'none' ? 'cancelled' : source.outcome}`}
                  data-testid="bring-source-outcome"
                >
                  {source.outcome === 'none' ? 'never ran' : source.outcome}
                </span>
                <span className="faint">
                  {source.runs} run{source.runs === 1 ? '' : 's'}
                </span>
                {running && (
                  <span className="caution" data-testid="bring-source-running">
                    still running
                  </span>
                )}
              </li>
            );
          })}
        </ul>

        {busy.length > 0 && (
          <p className="caution" role="alert" data-testid="bring-blocked-running">
            <AlertTriangle size={12} /> {busy.map((s) => s.name).join(', ')}{' '}
            {busy.length === 1 ? 'is' : 'are'} still running. A wrap-up captures what a session has produced,
            and that is still changing — stop it, or wait for it to finish.
          </p>
        )}

        <label className="bring-field">
          <span className="bring-label">What should the new session do?</span>
          <textarea
            className="composer-input"
            value={instructions}
            maxLength={WRAPUP_MAX_INSTRUCTIONS}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="Write the brief. The captured work is what it reads; this is what it is asked to do with it."
            rows={4}
            data-testid="bring-instructions"
          />
        </label>

        <details className="bring-files">
          <summary data-testid="bring-files-toggle">
            Carry files in{files.length > 0 ? ` · ${files.length} chosen` : ''}
          </summary>
          <p className="faint">
            Optional, and text only. Each session's own folder is listed below; up to {WRAPUP_MAX_FILES}{' '}
            files, {formatBytes(WRAPUP_MAX_FILE_BYTES)} each and {formatBytes(WRAPUP_MAX_TOTAL_BYTES)} in
            total, are copied into the package with a hash of the bytes that were copied.
          </p>
          {rows.map((source) => (
            <SourceFiles
              key={source.laneId}
              laneId={source.laneId}
              name={source.name}
              picked={files}
              atLimit={files.length >= WRAPUP_MAX_FILES}
              bytesLeft={WRAPUP_MAX_TOTAL_BYTES - totalBytes}
              onToggle={toggleFile}
            />
          ))}
        </details>

        {needsAck && (
          <label className="check bring-ack" data-testid="bring-ack">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
            />
            <span className="check-text">
              {unfinished.map((s) => s.name).join(', ')} did not finish. Bring in what{' '}
              {unfinished.length === 1 ? 'it' : 'they'} produced anyway, as partial work.
            </span>
          </label>
        )}

        {create.error && (
          <p className="error" role="alert" data-testid="bring-create-error">
            {create.error.message}
          </p>
        )}

        <div className="dialog-actions">
          <button
            type="button"
            className="btn"
            disabled={create.isPending}
            onClick={onClose}
            data-testid="bring-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={blocked}
            onClick={() =>
              create.mutate({
                projectRoot,
                // The revision each choice is pinned to: the server refuses the capture if a
                // session moved on between this list being read and this button being pressed.
                sources: rows.map((s) => ({ laneId: s.laneId, revision: s.revision })),
                instructions,
                files: files.map((f) => ({ laneId: f.laneId, path: f.path })),
                acknowledgePartial: acknowledged,
              })
            }
            data-testid="bring-submit"
          >
            {create.isPending ? 'Capturing…' : 'Bring together'}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * One source session's folder, for choosing files out of.
 *
 * The same bounded listing the Files pane uses: a session id and a relative path go to the server,
 * which decides what may be read (`resolveInWorkspace`). Files that cannot be carried — binaries,
 * oversized ones, escaping symlinks — are shown disabled with the reason, not hidden.
 */
function SourceFiles({
  laneId,
  name,
  picked,
  atLimit,
  bytesLeft,
  onToggle,
}: {
  laneId: string;
  name: string;
  picked: PickedFile[];
  atLimit: boolean;
  bytesLeft: number;
  onToggle(file: PickedFile): void;
}) {
  const [dir, setDir] = useState('');
  const listing = trpc.outputs.list.useQuery({ laneId, path: dir });
  const parts = dir === '' ? [] : dir.split('/');
  return (
    <section className="bring-source-files" data-testid="bring-source-files" data-lane={laneId}>
      <header className="bring-files-head">
        <span className="truncate">{name}</span>
        <nav className="outputs-crumbs" aria-label={`Folder in ${name}`}>
          <button type="button" className="outputs-crumb" onClick={() => setDir('')}>
            Workspace
          </button>
          {parts.map((part, i) => (
            <span key={parts.slice(0, i + 1).join('/')}>
              <ChevronRight size={11} aria-hidden="true" />
              <button
                type="button"
                className="outputs-crumb"
                onClick={() => setDir(parts.slice(0, i + 1).join('/'))}
              >
                {part}
              </button>
            </span>
          ))}
        </nav>
      </header>
      {listing.isPending && <p className="faint">Reading…</p>}
      {listing.error && (
        <p className="error" role="alert">
          {listing.error.message}
        </p>
      )}
      {listing.data?.entries.length === 0 && <p className="faint">Nothing here.</p>}
      <ul className="bring-entries">
        {listing.data?.entries.map((entry) => {
          if (entry.kind === 'directory')
            return (
              <li key={entry.path}>
                <button
                  type="button"
                  className="outputs-entry"
                  onClick={() => setDir(entry.path)}
                  data-testid="bring-entry-dir"
                >
                  <Folder size={13} />
                  <span className="truncate">{entry.name}</span>
                </button>
              </li>
            );
          const text = entry.preview === 'text' || entry.preview === 'markdown';
          const chosen = picked.some((f) => f.laneId === laneId && f.path === entry.path);
          const tooBig = entry.size > WRAPUP_MAX_FILE_BYTES;
          const overBudget = !chosen && entry.size > bytesLeft;
          const disabled = !chosen && (entry.blocked === true || !text || tooBig || overBudget || atLimit);
          return (
            <li key={entry.path}>
              <label
                className={`bring-entry${disabled ? ' disabled' : ''}`}
                data-testid="bring-entry-file"
                data-path={entry.path}
                title={
                  entry.blocked
                    ? 'A symbolic link pointing outside this session’s folder; it cannot be read here.'
                    : !text
                      ? 'Only text files can be carried into a wrap-up.'
                      : tooBig
                        ? `Larger than the ${formatBytes(WRAPUP_MAX_FILE_BYTES)} limit for one file.`
                        : overBudget
                          ? 'Over what is left of this package’s total size.'
                          : atLimit && !chosen
                            ? `Already carrying ${WRAPUP_MAX_FILES} files.`
                            : entry.path
                }
              >
                <input
                  type="checkbox"
                  checked={chosen}
                  disabled={disabled}
                  onChange={() => onToggle({ laneId, path: entry.path, bytes: entry.size })}
                />
                {entry.blocked ? <Link2Off size={13} /> : <File size={13} />}
                <span className="truncate">{entry.name}</span>
                <span className="faint mono">{formatBytes(entry.size)}</span>
              </label>
            </li>
          );
        })}
      </ul>
      {listing.data?.truncated && (
        <p className="faint">Only the first entries are listed; this folder has more.</p>
      )}
    </section>
  );
}
