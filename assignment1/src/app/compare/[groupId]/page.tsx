import { Compare } from '@/ui/components/compare/Compare';

export default async function Page({ params }: { params: Promise<{ groupId: string }> }) {
  const { groupId } = await params;
  return <Compare groupId={groupId} />;
}
