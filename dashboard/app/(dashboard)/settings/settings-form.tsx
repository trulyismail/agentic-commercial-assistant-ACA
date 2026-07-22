"use client";

import { useActionState, useEffect, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { saveSettings, type SettingsState } from "./actions";

const GROUPS: { title: string; keys: string[] }[] = [
  { title: "Réservation", keys: ["CALENDLY_URL"] },
  { title: "Routage support", keys: ["SUPPORT_EMAIL", "SUPPORT_SLACK_WEBHOOK_URL"] },
  { title: "Routage RH", keys: ["HR_EMAIL", "HR_SLACK_WEBHOOK_URL"] },
  { title: "Relances", keys: ["RELANCE_DAYS", "RELANCE_MAX_ROUNDS"] },
  { title: "Conservation des données", keys: ["RETENTION_DAYS"] },
];

const initialState: SettingsState = { error: null, saved: false };

export function SettingsForm({
  schema,
  values,
}: {
  schema: Record<string, string>;
  values: Record<string, string>;
}) {
  const [state, formAction, pending] = useActionState(saveSettings, initialState);
  const [savedFlash, setSavedFlash] = useState(false);

  useEffect(() => {
    if (state.saved) {
      setSavedFlash(true);
      const t = setTimeout(() => setSavedFlash(false), 2400);
      return () => clearTimeout(t);
    }
  }, [state.saved]);

  return (
    <form action={formAction} className="max-w-2xl space-y-8">
      {GROUPS.map((group) => (
        <div key={group.title}>
          <h3 className="mb-3 font-mono text-xs uppercase tracking-[0.14em] text-muted">{group.title}</h3>
          <div className="space-y-3 rounded-xl border border-line bg-ink-850 p-4">
            {group.keys.map((key) => (
              <div key={key}>
                <label htmlFor={key} className="mb-1 block text-sm text-paper-dim">
                  {schema[key] ?? key}
                </label>
                <input
                  id={key}
                  name={key}
                  defaultValue={values[key] ?? ""}
                  placeholder="(valeur .env par défaut si laissé vide)"
                  className="w-full rounded-lg border border-line bg-ink-900 px-3 py-2 text-sm text-paper placeholder:text-muted-dim outline-none focus:border-teal-400"
                />
              </div>
            ))}
          </div>
        </div>
      ))}

      {state.error && (
        <p className="rounded-md border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-sm text-rose-300">
          {state.error}
        </p>
      )}

      <div className="flex items-center gap-4">
        <button
          type="submit"
          disabled={pending}
          className="rounded-lg bg-teal-400 px-5 py-2.5 text-sm font-medium text-ink-950 transition-transform active:scale-[0.98] disabled:opacity-50"
        >
          {pending ? "Enregistrement…" : "Enregistrer les réglages"}
        </button>
        <AnimatePresence>
          {savedFlash && (
            <motion.span
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              className="text-sm text-teal-300"
            >
              Enregistré — pris en compte à la prochaine analyse.
            </motion.span>
          )}
        </AnimatePresence>
      </div>
    </form>
  );
}
