'use client';
import { AlertTriangle, GitBranch } from 'lucide-react';
import { useEffect } from 'react';
import type { LaneDto } from '@/core/types';
import { useDialogFocus } from '@/ui/components/chrome/useDialogFocus';
import { SessionForm } from '@/ui/components/session/SessionForm';
import { trpc } from '@/ui/trpc/client';

/**
 * Another conversation — by default in the same folder, which is what "new session" usually means.
 *
 * The same form Home shows, so there is one creation flow rather than three. It defaults to this
 * session's folder and inherits its project, and the isolated option is available when that folder
 * is a git repository. An ordinary folder is not an error here: it simply has no checkout to make,
 * and the form says so quietly instead of refusing.
 *
 * A new session is a new conversation. It does not inherit this one's memory — continuing *this*
 * conversation is what the composer does.
 *
 * The note below is the honest half of the isolated option, shown before anything is created:
 * `git worktree add <commit>` copies commits and not uncommitted edits, and this console will not
 * commit an operator's work for them to improve that number.
 */
type NewSessionContext = { from: LaneDto; projectRoot?: never } | { from?: never; projectRoot: string };

export function NewSessionDialog({ from, projectRoot, onClose }: NewSessionContext & { onClose(): void }) {
  const dialog = useDialogFocus<HTMLDivElement>();
  const preview = trpc.workspaces.preview.useQuery(
    from ? { fromLaneId: from.id } : { repoRoot: projectRoot },
  );
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const data = preview.data;
  const gitRepo = data?.isGitRepo === true ? data : null;
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: scrim closes on click; Escape is handled above.
    <div className="scrim" onClick={onClose} role="presentation">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-session-title"
        ref={dialog.ref}
        onKeyDown={dialog.onKeyDown}
        onClick={(e) => e.stopPropagation()}
        data-testid="new-session-dialog"
      >
        <h2 id="new-session-title">New session</h2>
        <p className="muted">
          {from
            ? `A separate conversation in this session's folder. ${from.name} keeps its work and context.`
            : "A fresh conversation in this project's folder."}
        </p>

        {gitRepo && (
          <dl className="kv" data-testid="new-session-base">
            <div className="kv-row">
              <dt>A checkout would start from</dt>
              <dd className="mono" data-testid="new-session-commit">
                <GitBranch size={12} /> {gitRepo.baseCommit.slice(0, 10)}
                {gitRepo.subject ? ` — ${gitRepo.subject}` : ''}
              </dd>
            </div>
          </dl>
        )}
        {gitRepo && gitRepo.dirtyFiles > 0 && (
          <p className="caution" data-testid="new-session-dirty">
            <AlertTriangle size={12} />{' '}
            {gitRepo.dirtyFiles === 1 ? '1 file is' : `${gitRepo.dirtyFiles} files are`} modified or untracked
            here and would <strong>not</strong> be copied into a checkout of its own — only the commit above
            comes across.
          </p>
        )}

        <SessionForm from={from} initialCwd={projectRoot} autoFocus onCancel={onClose} />
      </div>
    </div>
  );
}
