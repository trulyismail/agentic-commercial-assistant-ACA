"use client";

import { motion } from "motion/react";
import type { NodeId } from "@/lib/graph-topology";

const AGENTS: { id: NodeId; name: string; role: string; accent: string }[] = [
  { id: "supervisor", name: "Superviseur", role: "Oriente l'équipe, arbitre l'ordre des agents", accent: "var(--violet-400)" },
  { id: "enrichissement", name: "Enrichissement", role: "Profil entreprise (web + cache)", accent: "var(--teal-400)" },
  { id: "connaissance", name: "Connaissance", role: "RAG sémantique sur la base de connaissances", accent: "var(--teal-400)" },
  { id: "veille", name: "Veille", role: "Recherche web quand la FAQ est muette", accent: "var(--teal-400)" },
  { id: "stratege", name: "Stratège", role: "Rédige la proposition, relue par Réflexion", accent: "var(--amber-400)" },
];

export function AgentRoster({ activeCounts }: { activeCounts: Record<string, number> }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      {AGENTS.map((agent, i) => {
        const count = activeCounts[agent.id] ?? 0;
        return (
          <motion.div
            key={agent.id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05, duration: 0.4 }}
            className="relative overflow-hidden rounded-xl border border-line bg-ink-850 p-4"
          >
            <div className="mb-2 flex items-center justify-between">
              <span
                className="h-2 w-2 rounded-full"
                style={{ background: count > 0 ? agent.accent : "var(--muted-dim)" }}
              />
              {count > 0 && (
                <span className="rounded-full bg-amber-400/15 px-2 py-0.5 font-mono text-[10px] text-amber-300">
                  {count} en cours
                </span>
              )}
            </div>
            <p className="font-display text-sm font-semibold text-paper">{agent.name}</p>
            <p className="mt-1 text-xs leading-snug text-muted">{agent.role}</p>
          </motion.div>
        );
      })}
    </div>
  );
}
