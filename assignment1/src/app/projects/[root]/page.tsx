import { notFound } from 'next/navigation';
import { ProjectView } from '@/ui/components/session/ProjectView';
import { decodeProjectRoot } from '@/ui/project';

export default async function Page({ params }: { params: Promise<{ root: string }> }) {
  const { root } = await params;
  const projectRoot = decodeProjectRoot(root);
  // A link that was not built by `projectHref` — a stale bookmark, a hand-edited URL — is a page
  // that is not here, not a server error.
  if (projectRoot === null) notFound();
  return <ProjectView projectRoot={projectRoot} />;
}
