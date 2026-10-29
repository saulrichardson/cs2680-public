'use client';
import { ChevronUp, Folder, FolderGit2, FolderPlus } from 'lucide-react';
import { useState } from 'react';
import { useDebouncedValue } from '@/ui/hooks/useDebouncedValue';
import { trpc } from '@/ui/trpc/client';

/**
 * Choosing where the work happens — including making somewhere new.
 *
 * The field browses folders that exist; "New folder" creates one and selects it, so an operator
 * with nothing set up yet does not have to leave for a terminal. It is one validated name in a
 * visible parent, never a `mkdir -p` of whatever was typed into the field: a mistyped path should
 * stay a mistake, not quietly become a real directory the agent then works in.
 */
export function DirectoryPicker({
  value,
  onChange,
  testId = 'lane-cwd',
}: {
  value: string;
  onChange: (v: string) => void;
  testId?: string;
}) {
  const [browsing, setBrowsing] = useState(false);
  const [creating, setCreating] = useState(false);
  const debouncedValue = useDebouncedValue(value, 250);
  // The field starts empty so nothing is chosen by accident, but an empty listing would be a useless
  // place to start navigating from: with nothing typed, browse the home directory. Choosing a
  // subdirectory fills the field, so the listing follows the input from then on. (`system.info` is
  // already fetched by both dialogs; React Query serves this from the same cache entry.)
  const home = trpc.system.info.useQuery().data?.home;
  const target = debouncedValue.trim() || home || '';
  const browse = trpc.fs.browse.useQuery({ path: target || '/' }, { enabled: browsing && target !== '' });
  return (
    <div className="dirpicker">
      <div className="dirpicker-row">
        <input
          className="input mono"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Pick a project directory…"
          data-testid={testId}
          spellCheck={false}
        />
        <button type="button" className="btn" onClick={() => setBrowsing((b) => !b)}>
          {browsing ? 'Hide' : 'Browse'}
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => setCreating((c) => !c)}
          data-testid="dirpicker-new"
        >
          <FolderPlus size={14} /> New folder
        </button>
      </div>
      {creating && (
        <NewFolder
          // Inside the folder being browsed when there is one, so "New folder" means what it looks
          // like it means; otherwise the console's own scratch root.
          parent={browsing && browse.data?.exists ? browse.data.path : undefined}
          onCreated={(path) => {
            onChange(path);
            setCreating(false);
            setBrowsing(false);
          }}
          onCancel={() => setCreating(false)}
        />
      )}
      {browsing && browse.isPending && (
        <span className="faint" data-testid="dirpicker-loading">
          Looking…
        </span>
      )}
      {browsing && browse.error && (
        <span className="error" role="alert" data-testid="dirpicker-failed">
          Could not read that directory — {browse.error.message}
        </span>
      )}
      {browsing && browse.data && (
        <div className="dirpicker-list" data-testid="dirpicker-list">
          <div className="dirpicker-head">
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => onChange(browse.data.parent)}
              title="Up one level"
            >
              <ChevronUp size={14} />
            </button>
            <span className="truncate mono">{browse.data.path}</span>
            {browse.data.isGitRepo && <span className="tag">git</span>}
            {/* `fs.browse` tells the cases apart — unreadable, missing, not a directory — so the
                listing says which one rather than the one-size-fits-all "not a directory". */}
            {browse.data.error && (
              <span className="pill pill-failed" data-testid="dirpicker-error">
                {browse.data.error}
              </span>
            )}
          </div>
          <ul>
            {browse.data.dirs.map((d) => (
              <li key={d}>
                <button
                  type="button"
                  className="dirpicker-item"
                  onClick={() => onChange(`${browse.data.path.replace(/\/$/, '')}/${d}`)}
                >
                  <Folder size={14} /> {d}
                </button>
              </li>
            ))}
            {browse.data.dirs.length === 0 && <li className="faint">No subdirectories</li>}
            {browse.data.truncated && (
              <li className="faint" data-testid="dirpicker-truncated">
                Showing the first 500 folders
              </li>
            )}
          </ul>
        </div>
      )}
      {!browsing && browse.data?.isGitRepo && (
        <span className="faint">
          <FolderGit2 size={12} /> git repository
        </span>
      )}
    </div>
  );
}

/** One name, one visible parent, one folder. The server validates the name again. */
function NewFolder({
  parent,
  onCreated,
  onCancel,
}: {
  parent: string | undefined;
  onCreated(path: string): void;
  onCancel(): void;
}) {
  const suggestion = trpc.fs.scratchSuggestion.useQuery(parent ? { parent } : {});
  const create = trpc.fs.createFolder.useMutation();
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const suggested = suggestion.data?.name ?? '';
  const effective = name.trim() || suggested;
  const submit = async () => {
    if (!effective) return;
    setError(null);
    try {
      const { path } = await create.mutateAsync({ ...(parent ? { parent } : {}), name: effective });
      onCreated(path);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <div className="dirpicker-new" data-testid="dirpicker-new-form">
      <span className="faint mono truncate" data-testid="dirpicker-new-parent">
        {suggestion.data?.parent ?? '…'}/
      </span>
      <input
        className="input mono"
        value={name}
        placeholder={suggested}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            void submit();
          }
          if (e.key === 'Escape') onCancel();
        }}
        aria-label="New folder name"
        data-testid="dirpicker-new-name"
        spellCheck={false}
      />
      <button
        type="button"
        className="btn btn-primary"
        disabled={create.isPending || !effective}
        onClick={() => void submit()}
        data-testid="dirpicker-new-create"
      >
        Create
      </button>
      <button type="button" className="btn btn-ghost" onClick={onCancel}>
        Cancel
      </button>
      {error && (
        <span className="error" role="alert" data-testid="dirpicker-new-error">
          {error}
        </span>
      )}
    </div>
  );
}
