'use client';
import { ExternalLink, FileText, PackageOpen } from 'lucide-react';
import Link from 'next/link';
import type { CapturedSource, WrapUpDto } from '@/core/wrapup';
import { formatBytes, formatClock } from '@/ui/format';
import { useMissionStore } from '@/ui/store/missionStore';

/**
 * What a wrap-up was given, as the console recorded it at capture.
 *
 * Read from the wrap-up's own rows, never by re-reading the sources: a source renamed, continued or
 * archived since does not change a word here. Results are not here — they are what this session's
 * own agent wrote, and they are in **Files**.
 */
export function WrapUpInputs({ laneId, wrapUp }: { laneId: string; wrapUp: WrapUpDto }) {
  const showPane = useMissionStore((s) => s.showPane);
  const files = wrapUp.sources.reduce((n, s) => n + s.files.length, 0);
  return (
    <section className="wrapup" data-testid="wrapup-inputs">
      <header className="wrapup-head">
        <h2>
          <PackageOpen size={15} aria-hidden="true" /> Captured inputs
        </h2>
        <p className="muted">
          Brought together from {wrapUp.sources.length} session{wrapUp.sources.length === 1 ? '' : 's'} on{' '}
          {formatClock(wrapUp.capturedAt)}. {files === 0 ? 'No files were carried in' : null}
          {files > 0 ? `${files} file${files === 1 ? ' was' : 's were'} copied in` : null}, and the package
          itself is in this session's folder under <code>inputs/</code>.
        </p>
        {wrapUp.partial && (
          <p className="caution" data-testid="wrapup-partial">
            At least one source had not finished when it was captured, so the work it contributed is partial.
          </p>
        )}
      </header>

      <section className="wrapup-section">
        <h3>What this session was asked to do</h3>
        <p className="wrapup-instructions" data-testid="wrapup-instructions">
          {wrapUp.instructions}
        </p>
      </section>

      <section className="wrapup-section">
        <h3>Sources</h3>
        <ul className="wrapup-sources">
          {wrapUp.sources.map((source) => (
            <li key={source.laneId}>
              <SourceCard
                source={source}
                onOpenFile={(path) => showPane(laneId, { kind: 'outputs', path })}
              />
            </li>
          ))}
        </ul>
      </section>

      <details className="wrapup-note faint">
        <summary>About these hashes</summary>
        <p>
          SHA-256 of the bytes copied at capture. It records which bytes went in — not that either copy has
          stayed that way. The package is an ordinary folder this session's agent can reach.
        </p>
      </details>
    </section>
  );
}

/** How a source's outcome at capture is worded — the console's own status vocabulary. */
function outcomeLabel(outcome: CapturedSource['outcome']): string {
  return outcome === 'none' ? 'never ran' : outcome;
}

function SourceCard({ source, onOpenFile }: { source: CapturedSource; onOpenFile(path: string): void }) {
  const pill = source.outcome === 'none' ? 'cancelled' : source.outcome;
  return (
    <article className="wrapup-source" data-testid="wrapup-source" data-lane={source.laneId}>
      <header className="wrapup-source-head">
        <span className="wrapup-source-ordinal faint">{source.ordinal}</span>
        <span className="wrapup-source-name truncate">{source.name}</span>
        <span className={`pill pill-${pill}`} data-testid="wrapup-source-outcome">
          {outcomeLabel(source.outcome)} when captured
        </span>
        <span className="spacer" />
        {source.present ? (
          <Link
            href={`/lanes/${source.laneId}`}
            className="btn btn-ghost btn-small"
            data-testid="wrapup-source-link"
          >
            <ExternalLink size={12} /> Open
          </Link>
        ) : (
          <span className="faint" data-testid="wrapup-source-gone">
            no longer in this console
          </span>
        )}
      </header>
      {source.runs.length === 0 ? (
        <p className="faint">This session had never executed anything, so nothing of its own was captured.</p>
      ) : (
        <ol className="wrapup-runs">
          {source.runs.map((run) => (
            <li key={run.runId}>
              <span className={`pill pill-${run.status}`}>{run.status}</span>
              <span className="truncate" title={run.prompt}>
                {run.prompt}
              </span>
              <span className="faint mono">{run.runId}</span>
            </li>
          ))}
        </ol>
      )}
      {source.files.length > 0 && (
        <ul className="wrapup-files">
          {source.files.map((file) => (
            <li key={file.storedPath}>
              {/* The copy, not the original: this opens what the wrap-up was given, in this
                  session's own Files. The source's folder is reachable from the source. */}
              <button
                type="button"
                className="wrapup-file"
                onClick={() => onOpenFile(`inputs/${file.storedPath}`)}
                data-testid="wrapup-file"
                title={`Open the copy this session was given (from ${file.path})`}
              >
                <FileText size={12} />
                <span className="truncate mono">{file.path}</span>
                <span className="faint">{formatBytes(file.bytes)}</span>
                <span className="faint mono wrapup-hash">{file.sha256.slice(0, 12)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
