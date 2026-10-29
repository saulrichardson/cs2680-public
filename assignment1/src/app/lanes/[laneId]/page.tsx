import { Focus } from '@/ui/components/focus/Focus';

export default async function Page({ params }: { params: Promise<{ laneId: string }> }) {
  const { laneId } = await params;
  return <Focus laneId={laneId} />;
}
