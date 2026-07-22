"use client";

import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { AgentRoster } from "./agent-roster";
import { ThreadDrawer } from "./thread-drawer";
import type { HistoryRow, ThreadSnapshot } from "@/lib/types";

const CATEGORY_COLOR: Record<string, string> = {
  DEMANDE_DEMO: "var(--teal-400)",
  DEVIS: "var(--violet-400)",
  SUPPORT: "var(--amber-400)",
  AUTRE: "var(--muted)",
  SPAM: "var(--rose-400)",
};

export function OverviewClient({
  initialPending,
  history,
}: {
  initialPending: ThreadSnapshot[];
  history: HistoryRow[];
}) {
  const [pending, setPending] = useState(initialPending);
  const [selected, setSelected] = useState<ThreadSnapshot | null>(null);

  const activeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const t of pending) {
      const agents = t.completed_agents ?? [];
      const last = agents[agents.length - 1];
      // Le "prochain à agir" plutôt que le dernier terminé : approx. simple mais honnête (le
      // superviseur décide dynamiquement, on ne connaît pas son prochain choix sans le graphe réel).
      counts[last ?? "supervisor"] = (counts[last ?? "supervisor"] ?? 0) + 1;
    }
    return counts;
  }, [pending]);

  function handleResolved(updated: ThreadSnapshot) {
    setPending((prev) => prev.filter((t) => t.thread_id !== updated.thread_id));
    setSelected(null);
  }

  return (
    <div className="space-y-8 p-8">
      <header>
        <h1 className="font-display text-2xl font-semibold text-paper">Vue d&apos;ensemble</h1>
        <p className="mt-1 text-sm text-muted">
          {pending.length} analyse{pending.length !== 1 ? "s" : ""} en attente de votre décision.
        </p>
      </header>

      <section>
        <SectionLabel>Équipe d&apos;agents</SectionLabel>
        <AgentRoster activeCounts={activeCounts} />
      </section>

      <section>
        <SectionLabel>À traiter</SectionLabel>
        {pending.length === 0 ? (
          <EmptyState message="Rien en attente — l'équipe a tout traité, ou personne n'a encore écrit." />
        ) : (
          <div className="space-y-2">
            {pending.map((t, i) => (
              <motion.button
                key={t.thread_id}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.03 }}
                onClick={() => setSelected(t)}
                className="flex w-full items-center justify-between gap-4 rounded-xl border border-line bg-ink-850 px-4 py-3 text-left transition-colors hover:border-teal-400/40"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-paper-dim">{t.subject || "(sans objet)"}</p>
                  <p className="truncate text-xs text-muted">{t.sender}</p>
                </div>
                {t.classification && (
                  <span
                    className="shrink-0 rounded-full px-2.5 py-1 font-mono text-[11px]"
                    style={{
                      background: `${CATEGORY_COLOR[t.classification] ?? "var(--muted)"}22`,
                      color: CATEGORY_COLOR[t.classification] ?? "var(--muted)",
                    }}
                  >
                    {t.classification}
                  </span>
                )}
                {t.pending_clarification && (
                  <span className="shrink-0 rounded-full bg-amber-400/15 px-2.5 py-1 font-mono text-[11px] text-amber-300">
                    précision requise
                  </span>
                )}
              </motion.button>
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionLabel>Historique récent</SectionLabel>
        {history.length === 0 ? (
          <EmptyState message="Aucune validation pour le moment." />
        ) : (
          <div className="overflow-hidden rounded-xl border border-line">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-ink-850 text-left text-xs uppercase tracking-wide text-muted">
                  <th className="px-4 py-2.5 font-medium">Expéditeur</th>
                  <th className="px-4 py-2.5 font-medium">Classification</th>
                  <th className="px-4 py-2.5 font-medium">Validé par</th>
                  <th className="px-4 py-2.5 font-medium">Date</th>
                </tr>
              </thead>
              <tbody>
                {history.slice(0, 12).map((row) => (
                  <tr key={row.thread_id} className="border-t border-line-soft">
                    <td className="max-w-[220px] truncate px-4 py-2.5 text-paper-dim">{row.sender}</td>
                    <td className="px-4 py-2.5">
                      <span
                        className="rounded-full px-2 py-0.5 font-mono text-[11px]"
                        style={{
                          background: `${CATEGORY_COLOR[row.classification] ?? "var(--muted)"}22`,
                          color: CATEGORY_COLOR[row.classification] ?? "var(--muted)",
                        }}
                      >
                        {row.classification}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-muted">{row.validated_by || "—"}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted">{row.validated_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <AnimatePresence>
        {selected && (
          <ThreadDrawer
            key={selected.thread_id}
            thread={selected}
            onClose={() => setSelected(null)}
            onResolved={handleResolved}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-3 font-mono text-xs uppercase tracking-[0.14em] text-muted">{children}</h2>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-8 text-center text-sm text-muted">
      {message}
    </div>
  );
}
