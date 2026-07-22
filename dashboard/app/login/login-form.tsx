"use client";

import { useActionState } from "react";
import { motion } from "motion/react";
import { login, type LoginState } from "./actions";

const initialState: LoginState = { error: null };

export function LoginForm({ next }: { next: string }) {
  const [state, formAction, pending] = useActionState(login, initialState);

  return (
    <motion.form
      action={formAction}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, delay: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className="w-full max-w-sm space-y-5"
    >
      <input type="hidden" name="next" value={next} />
      <div className="space-y-2">
        <label htmlFor="password" className="text-xs uppercase tracking-[0.14em] text-muted font-mono">
          Mot de passe du cockpit
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoFocus
          required
          placeholder="••••••••"
          className="w-full rounded-lg border border-line bg-ink-900/80 px-4 py-3 text-paper placeholder:text-muted-dim outline-none transition-colors focus:border-teal-400"
        />
      </div>

      {state.error && (
        <motion.p
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-md border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-sm text-rose-300"
          role="alert"
        >
          {state.error}
        </motion.p>
      )}

      <button
        type="submit"
        disabled={pending}
        className="group relative w-full overflow-hidden rounded-lg bg-teal-400 px-4 py-3 font-medium text-ink-950 transition-transform active:scale-[0.98] disabled:opacity-60"
      >
        <span className="relative z-10">{pending ? "Vérification…" : "Entrer dans le cockpit"}</span>
        <span className="absolute inset-0 -translate-x-full bg-teal-300 transition-transform duration-300 group-hover:translate-x-0" />
      </button>
    </motion.form>
  );
}
