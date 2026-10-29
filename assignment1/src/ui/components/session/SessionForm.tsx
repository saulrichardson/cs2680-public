'use client';
import { AlertTriangle, GitBranch, Sparkles } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import type { LaneDto } from '@/core/types';
import { useShortcutHints } from '@/ui/components/chrome/useShortcutHints';
import { DirectoryPicker } from '@/ui/components/session/DirectoryPicker';
import { projectDirProblem } from '@/ui/directory';
import { useDebouncedValue } from '@/ui/hooks/useDebouncedValue';
import { useMissionStore } from '@/ui/store/missionStore';
import { useToasts } from '@/ui/store/toasts';
import { trpc } from '@/ui/trpc/client';

/**
 * Starting a session — the only way the console creates one.
 *
 * Home, an empty project and "New session" inside a conversation render this same form, because
 * they were the same operation described three different ways. There is one question — which
 * folder — and one consequential option, whether the agent works in that folder or in a checkout
 * of it.
 *
 * Deliberately absent: a permission control (standard tools, by policy) and a browser control
 * (always on, by policy). Both were switches that looked like status and changed how the agent
 * runs.
 */
export function SessionForm({
  /** The session this was opened from: its folder is the default, and its project is inherited. */
  from,
  /** The folder to start in when there is no source session — an empty project's own folder. */
  initialCwd,
  autoFocus,
  onCancel,
}: {
  from?: LaneDto | undefined;
  initialCwd?: string | undefined;
  autoFocus?: boolean;
  onCancel?: () => void;
}) {
  const router = useRouter();
  const hints = useShortcutHints();
  const info = trpc.system.info.useQuery();
  const [cwd, setCwd] = useState(from?.cwd ?? initialCwd ?? '');
  const [prompt, setPrompt] = useState('');
  const [isolated, setIsolated] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const debouncedCwd = useDebouncedValue(cwd, 250);
  const settled = debouncedCwd.trim() === cwd.trim();
  const folder = debouncedCwd.trim();
  const probe = trpc.fs.browse.useQuery({ path: folder || '/' }, { enabled: folder !== '' });
  const isGitRepo = settled ? (probe.data?.isGitRepo ?? false) : false;
  // A folder that is not a git repository has no checkout to make, so the choice is not offered
  // and — this is the part that used to go wrong — is not left selected either.
  const isolating = isolated && isGitRepo;

  // Who else works in *this folder*, asked of the folder rather than of the project: a second
  // conversation inside an isolated session's checkout shares its files just as two ordinary
  // sessions in one folder do. The source session is included, because it is usually the answer.
  const sharing = trpc.lanes.list.useQuery({ cwd: folder }, { enabled: settled && folder !== '' });
  const neighbours = isolating ? [] : (sharing.data ?? []);

  // What an isolated checkout would actually start from. Only asked for when it is being chosen:
  // an ordinary folder has no base commit, and reporting one would be noise on the common path.
  const preview = trpc.workspaces.preview.useQuery(
    from && from.cwd === cwd.trim() ? { fromLaneId: from.id } : { repoRoot: folder || '/' },
    { enabled: isolating && settled },
  );
  const base = preview.data?.isGitRepo === true ? preview.data : null;

  const problem = projectDirProblem(cwd, info.data?.home);
  const caution =
    !problem && probe.data?.error && settled
      ? `${probe.data.path} ${probe.data.error} — a run here will fail immediately`
      : null;

  const utils = trpc.useUtils();
  const createLane = trpc.lanes.create.useMutation();
  const createWorkspace = trpc.workspaces.create.useMutation();
  const startRun = trpc.runs.start.useMutation();

  const ready = cwd.trim() !== '' && prompt.trim() !== '' && problem === null && !starting;

  /**
   * Create the session and send the first prompt, then go to it.
   *
   * Two mutations rather than one: creating the session and starting a run have separate failure
   * modes, and a session that exists with no run is a state the operator can see and retry from.
   * A run that fails to start still lands them in the session, with the error.
   */
  const start = async () => {
    if (!ready) return;
    setStarting(true);
    setError(null);
    let lane: LaneDto;
    try {
      // `isolating`, not `isolated`: the folder may have changed since the choice was made, and
      // sending `workspaces.create` at an ordinary folder is a git-only dead end.
      lane = isolating
        ? (
            await createWorkspace.mutateAsync(
              from && from.cwd === cwd.trim() ? { fromLaneId: from.id } : { repoRoot: cwd.trim() },
            )
          ).lane
        : await createLane.mutateAsync({ cwd: cwd.trim(), ...(from ? { fromLaneId: from.id } : {}) });
    } catch (err) {
      setStarting(false);
      setError(err instanceof Error ? err.message : String(err));
      return;
    }
    useMissionStore.getState().upsertLane(lane);
    utils.lanes.list.invalidate();
    utils.projects.list.invalidate();
    try {
      await startRun.mutateAsync({ laneId: lane.id, prompt: prompt.trim() });
    } catch (err) {
      useToasts.getState().push({
        kind: 'error',
        title: 'The session was created but the run did not start',
        text: err instanceof Error ? err.message : String(err),
      });
    }
    router.push(`/lanes/${lane.id}`);
  };

  return (
    <form
      className="session-form"
      data-testid="session-form"
      onSubmit={(e) => {
        e.preventDefault();
        void start();
      }}
    >
      {/* biome-ignore lint/a11y/noLabelWithoutControl: labels DirectoryPicker's own input (data-testid="lane-cwd"). */}
      <label className="field">
        <span>Folder</span>
        <DirectoryPicker
          value={cwd}
          onChange={(next) => {
            setCwd(next);
            // Choosing somewhere else un-chooses a checkout of somewhere else.
            setIsolated(false);
          }}
        />
        {problem ? (
          <span className="error" role="alert" data-testid="start-cwd-problem">
            {problem}
          </span>
        ) : caution ? (
          <span className="caution" data-testid="start-cwd-caution">
            <AlertTriangle size={12} /> {caution}
          </span>
        ) : neighbours.length > 0 ? (
          <SharingNotice lanes={neighbours} />
        ) : (
          <span className="faint">Point the agent at a scratch project, never at a folder with secrets.</span>
        )}
      </label>

      <label className="field">
        <span>Prompt</span>
        <textarea
          className="composer-input"
          rows={4}
          // biome-ignore lint/a11y/noAutofocus: the dialog form is opened expressly to type here.
          autoFocus={autoFocus}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault();
              void start();
            }
          }}
          placeholder="Ask the agent to do something in this folder…"
          data-testid="start-prompt"
        />
      </label>

      {/* Offered only when there is a checkout to make. On an ordinary folder the ordinary answer
          is the only answer, and a disabled radio explaining a git feature is noise. */}
      {isGitRepo && (
        <fieldset className="field">
          <legend>Files</legend>
          <label className="radio">
            <input
              type="radio"
              checked={!isolating}
              onChange={() => setIsolated(false)}
              data-testid="start-shared"
            />{' '}
            <span>
              <strong>This folder.</strong> The agent reads and edits these files.
            </span>
          </label>
          <label className="radio">
            <input
              type="radio"
              checked={isolating}
              onChange={() => setIsolated(true)}
              data-testid="start-isolated"
            />{' '}
            <span>
              <strong>A checkout of its own.</strong> A git worktree at this project's committed HEAD, so your
              working files are untouched.
            </span>
          </label>
          {isolating && (
            <div className="start-base" data-testid="start-base">
              {base ? (
                <>
                  <span className="mono" data-testid="start-base-commit">
                    <GitBranch size={12} /> {base.baseCommit.slice(0, 10)}
                    {base.subject ? ` — ${base.subject}` : ''}
                  </span>
                  {base.dirtyFiles > 0 ? (
                    <span className="caution" data-testid="start-base-dirty">
                      {base.dirtyFiles === 1 ? '1 file is' : `${base.dirtyFiles} files are`} modified or
                      untracked here and will <strong>not</strong> be copied.
                    </span>
                  ) : (
                    <span className="faint">
                      Nothing uncommitted here. Ignored files are never copied, so anything installed rather
                      than committed will not be there.
                    </span>
                  )}
                </>
              ) : (
                <span className="faint">Reading the checkout…</span>
              )}
            </div>
          )}
        </fieldset>
      )}

      {error && (
        <p className="error" role="alert" data-testid="start-error">
          {error}
        </p>
      )}
      <div className="start-actions">
        <button type="submit" className="btn btn-primary" disabled={!ready} data-testid="start-submit">
          <Sparkles size={14} /> {starting ? 'Starting…' : 'Start session'}{' '}
          <kbd className="kbd">{hints.submit}</kbd>
        </button>
        {onCancel && (
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

/**
 * Who else is writing here.
 *
 * Sharing a folder is the ordinary case, so this is a note and not a warning — but an agent
 * *currently running* in the same files is the thing worth saying out loud, and it is counted
 * from live run state rather than from the row.
 */
function SharingNotice({ lanes }: { lanes: LaneDto[] }) {
  // Select the stable store reference; building the Set inside the selector returns a new value
  // from every getSnapshot read, which React reports as error 185 (infinite update loop).
  const runs = useMissionStore((s) => s.runs);
  const running = new Set(
    Object.values(runs)
      .filter((r) => r.status === 'running')
      .map((r) => r.laneId),
  );
  const live = lanes.filter((l) => running.has(l.id)).length;
  const count = lanes.length === 1 ? '1 other session' : `${lanes.length} other sessions`;
  return (
    <span className={live > 0 ? 'caution' : 'faint'} data-testid="start-sharing" data-running={live}>
      {count} {lanes.length === 1 ? 'works' : 'work'} in this folder and{' '}
      {lanes.length === 1 ? 'shares' : 'share'} its files
      {live > 0 ? `, and ${live === 1 ? 'one is' : `${live} are`} running now.` : '.'}
    </span>
  );
}
