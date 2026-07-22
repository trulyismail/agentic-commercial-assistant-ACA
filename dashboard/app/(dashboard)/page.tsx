import { getPendingThreads, getThread, getThreadHistory } from "@/lib/aca";
import { OverviewClient } from "@/components/overview-client";
import type { ThreadSnapshot } from "@/lib/types";

export default async function OverviewPage() {
  const [pendingList, history] = await Promise.all([getPendingThreads(), getThreadHistory(20)]);

  // Le graphe/HITL a besoin de l'état complet de chaque thread (classification, brouillon,
  // completed_agents...) — queue_store ne stocke que des métadonnées légères (voir aca/api.py).
  const pendingSnapshots = await Promise.all(
    pendingList.map((p) => getThread(p.thread_id).catch(() => null))
  );

  return (
    <OverviewClient
      initialPending={pendingSnapshots.filter((s): s is ThreadSnapshot => s !== null)}
      history={history}
    />
  );
}
