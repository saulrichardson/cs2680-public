'use client';
import { memo } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { type LinkContext, resolveWorkspaceLink, workspaceFileUrl } from '@/core/workspaceLinks';

/**
 * Where a link in this markdown should go when it names a file rather than a web page.
 *
 * Present wherever the markdown was written *by an agent about its own workspace* — its final
 * response, and the documents in the Outputs pane. Absent elsewhere, in which case relative links
 * render as they always did.
 */
export interface WorkspaceLinks extends LinkContext {
  laneId: string;
  /** Open this workspace file in the console. The anchor still carries a real URL underneath. */
  onOpen(path: string): void;
}

export const TextBlock = memo(function TextBlock({
  markdown,
  links,
}: {
  markdown: string;
  links?: WorkspaceLinks;
}) {
  return (
    <div className="text-block" data-testid="text-block">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            const target = links ? resolveWorkspaceLink(href, links) : null;
            if (links && target?.kind === 'workspace') {
              const url = workspaceFileUrl(links.laneId, target.path);
              // A real URL underneath, so ⌘-click, "open in new tab" and the status bar all behave
              // — the response is served into an opaque sandboxed origin either way. A plain click
              // is intercepted and opens the file inside the console, which is what an operator
              // following a handoff index wants.
              return (
                <a
                  href={url}
                  data-workspace-path={target.path}
                  data-testid="workspace-link"
                  onClick={(e) => {
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
                    e.preventDefault();
                    links.onOpen(target.path);
                  }}
                >
                  {children}
                </a>
              );
            }
            // Everything else — an official source the research cited, an in-document anchor — is
            // left exactly as it was written.
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
          img: ({ src, alt }) => {
            const target = links && typeof src === 'string' ? resolveWorkspaceLink(src, links) : null;
            const resolved =
              target?.kind === 'workspace' && links ? workspaceFileUrl(links.laneId, target.path) : src;
            // biome-ignore lint/performance/noImgElement: a file an agent wrote at a path known only at runtime; next/image cannot optimize it and would lose the workspace route's sandboxed headers.
            return <img src={typeof resolved === 'string' ? resolved : undefined} alt={alt ?? ''} />;
          },
          pre: ({ children }) => <pre className="md-pre">{children}</pre>,
        }}
      >
        {markdown}
      </Markdown>
    </div>
  );
});
