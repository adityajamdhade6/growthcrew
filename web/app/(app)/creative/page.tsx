"use client";

import { useApi } from "@/lib/api";
import { label, useSession } from "@/lib/session";
import { Badge, Card, Empty, ErrorState, Loading, PageHeader, statusTone } from "@/components/ui";
import { AdImages } from "@/components/creatives";

type Item = { draft_id: number; piece: string; status: string; passed?: boolean; rounds: number };

export default function CreativeGallery() {
  const { workspace } = useSession();
  const list = useApi<Item[]>(`/workspaces/${workspace}/creatives`);
  if (list.loading) return <Loading rows={3} />;
  if (list.error) return <ErrorState error={list.error} retry={list.reload} />;
  return (
    <>
      <PageHeader title="Creative gallery" subtitle="Every ad rendered in 1:1, 4:5 and 9:16, with the vision critic's review." />
      {!list.data!.length ? (
        <Empty title="No ad images yet" body="Open an ad in the calendar and choose Make ad images." />
      ) : (
        <div className="space-y-4">
          {list.data!.map((item) => (
            <Card key={item.draft_id}>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <p className="font-medium">{item.piece}</p>
                <Badge tone={statusTone(item.status)}>{label(item.status)}</Badge>
              </div>
              <AdImages draftId={item.draft_id} disabled />
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
