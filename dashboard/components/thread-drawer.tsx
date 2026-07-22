"use client";

import { useState, useTransition } from "react";
import { motion } from "motion/react";
import { PulseGraph } from "./pulse-graph";
import { completedNodes, type NodeId } from "@/lib/graph-topology";
import type { ThreadSnapshot } from "@/lib/types";

const CATEGORY_COLOR: Record<string, string> = {
  DEMANDE_DEMO: "var(--teal-400)",
  DEVIS: "var(--violet-400)",
  SUPPORT: "var(--amber-400)",
  AUTRE: "var(--muted)",
  SPAM: "var(--rose-400)",
};

export function ThreadDrawer({
  thread,
  onClose,
  onResolved,
}: {
  thread: ThreadSnapshot;
  onClose: () => void;
  onResolved: (updated: ThreadSnapshot) => void;
}) {
  const [draft, setDraft] = useState(thread.draft_response ?? "");
  const [validatedBy, setValidatedBy] = useState("");
  const [clarification, setClarification] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const activeNode: NodeId | null = thread.pending_clarification ? "clarification" : null;

  function callApi(path: string, body?: unknown) {
    setError(null);
    startTransition(async () => {
      try {
        const res = await fetch(`/api/threads/${thread.thread_id}${path}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body ?? {}),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error ?? "Échec de la requête.");
        onResolved(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  return (
    <>
      <motion.div
        className="fixed inset-0 z-30 bg-ink-950/60 backdrop-blur-sm"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
      />
      <motion.aside
        className="scrollbar-thin fixed right-0 top-0 z-40 h-full w-full max-w-xl overflow-y-auto border-l border-line bg-ink-900 p-6"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", stiffness: 320, damping: 34 }}
        role="dialog"
        aria-label="Détail de l'analyse"
      >
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            <span
              className="mb-1.5 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono text-[11px]"
              style={{
                background: `${CATEGORY_COLOR[thread.classification ?? ""] ?? "var(--muted)"}22`,
                color: CATEGORY_COLOR[thread.classification ?? ""] ?? "var(--muted)",
              }}
            >
              {thread.classification ?? "…"}
              {thread.classification_confidence != null && (
                <span className="opacity-70">{Math.round(thread.classification_confidence * 100)}%</span>
              )}
            </span>
            <h2 className="font-display text-lg font-semibold text-paper">{thread.subject || "(sans objet)"}</h2>
            <p className="text-sm text-muted">{thread.sender}</p>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-2 text-muted transition-colors hover:bg-ink-800 hover:text-paper"
            aria-label="Fermer"
          >
            ✕
          </button>
        </div>

        <div className="mb-5 h-40 rounded-xl border border-line bg-ink-850">
          <PulseGraph
            mode="progress"
            activeNode={activeNode}
            doneNodes={completedNodes(thread.completed_agents)}
            className="h-full w-full"
          />
        </div>

        {thread.risk_flags && thread.risk_flags.length > 0 && (
          <div className="mb-4 rounded-lg border border-rose-400/30 bg-rose-400/10 px-3 py-2.5 text-sm text-rose-300">
            <strong className="font-medium">Clause(s) à risque : </strong>
            {thread.risk_flags.join(", ")}
          </div>
        )}
        {thread.knowledge_gap && (
          <div className="mb-4 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2.5 text-sm text-amber-300">
            Aucune réponse vérifiée trouvée pour ce besoin — relisez avant validation.
          </div>
        )}

        {thread.extracted_info && (
          <div className="mb-5 grid grid-cols-2 gap-3 rounded-xl border border-line bg-ink-850 p-4 text-sm">
            <Field label="Entreprise" value={thread.extracted_info.entreprise} />
            <Field label="Contact" value={thread.extracted_info.contact} />
            <Field label="Urgence" value={thread.extracted_info.urgence} />
            <Field label="Besoin" value={thread.extracted_info.besoin_principal} wide />
          </div>
        )}

        {thread.pending_clarification ? (
          <div className="mb-5 rounded-xl border border-amber-400/30 bg-amber-400/5 p-4">
            <p className="mb-2 text-sm text-paper-dim">{thread.pending_clarification}</p>
            <textarea
              value={clarification}
              onChange={(e) => setClarification(e.target.value)}
              rows={2}
              className="mb-3 w-full rounded-lg border border-line bg-ink-900 p-3 text-sm text-paper outline-none focus:border-teal-400"
              placeholder="Votre réponse…"
            />
            <button
              disabled={pending || !clarification.trim()}
              onClick={() => callApi("/clarifier", { answer: clarification })}
              className="rounded-lg bg-amber-400 px-4 py-2 text-sm font-medium text-ink-950 disabled:opacity-50"
            >
              Envoyer la précision
            </button>
          </div>
        ) : (
          <>
            <div className="mb-5">
              <label className="mb-1.5 block text-xs uppercase tracking-wide text-muted">
                Proposition (éditable)
              </label>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={8}
                className="w-full rounded-xl border border-line bg-ink-850 p-3 text-sm leading-relaxed text-paper outline-none focus:border-teal-400"
              />
            </div>

            {error && (
              <p className="mb-3 rounded-md border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">
                {error}
              </p>
            )}

            <input
              value={validatedBy}
              onChange={(e) => setValidatedBy(e.target.value)}
              placeholder="Validé par (traçabilité)"
              className="mb-3 w-full rounded-lg border border-line bg-ink-900 px-3 py-2 text-sm text-paper outline-none focus:border-teal-400"
            />

            <div className="flex gap-3">
              <button
                disabled={pending}
                onClick={() =>
                  callApi("/valider", {
                    validated_by: validatedBy || undefined,
                    edited_draft: draft !== thread.draft_response ? draft : undefined,
                  })
                }
                className="flex-1 rounded-lg bg-teal-400 px-4 py-2.5 text-sm font-medium text-ink-950 transition-transform active:scale-[0.98] disabled:opacity-50"
              >
                {pending ? "…" : "Valider"}
              </button>
              <button
                disabled={pending}
                onClick={() => callApi("/rejeter")}
                className="rounded-lg border border-rose-400/40 px-4 py-2.5 text-sm font-medium text-rose-300 transition-colors hover:bg-rose-400/10 disabled:opacity-50"
              >
                Rejeter
              </button>
            </div>
          </>
        )}

        {thread.reasoning_log && thread.reasoning_log.length > 0 && (
          <details className="mt-6 text-sm">
            <summary className="cursor-pointer text-muted hover:text-paper-dim">
              Raisonnement de l&apos;équipe ({thread.reasoning_log.length})
            </summary>
            <ul className="mt-2 space-y-1 border-l border-line pl-3 font-mono text-xs text-muted">
              {thread.reasoning_log.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </details>
        )}
      </motion.aside>
    </>
  );
}

function Field({ label, value, wide }: { label: string; value?: string | null; wide?: boolean }) {
  return (
    <div className={wide ? "col-span-2" : undefined}>
      <p className="text-[11px] uppercase tracking-wide text-muted">{label}</p>
      <p className="text-paper-dim">{value || "—"}</p>
    </div>
  );
}
