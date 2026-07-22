import { getStats } from "@/lib/aca";

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

const CATEGORY_COLOR: Record<string, string> = {
  DEMANDE_DEMO: "var(--teal-400)",
  DEVIS: "var(--violet-400)",
  SUPPORT: "var(--amber-400)",
  AUTRE: "var(--muted)",
  SPAM: "var(--rose-400)",
};

export default async function BillingPage() {
  const stats = await getStats(30);
  const medianResponse = median(stats.response_times.map((r) => r.minutes));
  const maxVolume = Math.max(1, ...stats.volume_by_category.map((v) => v.count));
  const funnelSteps = Object.entries(stats.funnel_counts);
  const funnelMax = Math.max(1, ...funnelSteps.map(([, v]) => v));

  return (
    <div className="space-y-8 p-8">
      <header>
        <h1 className="font-display text-2xl font-semibold text-paper">Facturation &amp; usage</h1>
        <p className="mt-1 text-sm text-muted">
          30 derniers jours. Le suivi de tokens est gratuit tant que Groq l&apos;est ; sert de base
          si la facturation Stripe (usage réel) est activée pour ce tenant.
        </p>
      </header>

      <section className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Kpi label="Tokens consommés" value={(stats.token_stats.total_entree + stats.token_stats.total_sortie).toLocaleString("fr-FR")} />
        <Kpi label="Moy. / analyse" value={stats.token_stats.moyenne_par_analyse.toLocaleString("fr-FR")} />
        <Kpi label="Taux d'édition" value={`${stats.edit_rate.taux_pct}%`} sub={`${stats.edit_rate.édités}/${stats.edit_rate.validés} brouillons`} />
        <Kpi
          label="Réponse médiane"
          value={medianResponse != null ? `${Math.round(medianResponse)} min` : "—"}
        />
      </section>

      <section>
        <SectionLabel>Volume par catégorie</SectionLabel>
        <div className="space-y-2.5 rounded-xl border border-line bg-ink-850 p-4">
          {stats.volume_by_category.length === 0 && <p className="text-sm text-muted">Aucune donnée pour le moment.</p>}
          {stats.volume_by_category.map((v) => (
            <div key={v.classification} className="flex items-center gap-3">
              <span className="w-28 shrink-0 font-mono text-xs text-muted">{v.classification}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-ink-900">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${(v.count / maxVolume) * 100}%`,
                    background: CATEGORY_COLOR[v.classification] ?? "var(--muted)",
                  }}
                />
              </div>
              <span className="w-8 shrink-0 text-right font-mono text-xs text-paper-dim">{v.count}</span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <SectionLabel>Entonnoir de conversion</SectionLabel>
        <div className="flex items-end gap-3 rounded-xl border border-line bg-ink-850 p-4">
          {funnelSteps.map(([label, count]) => (
            <div key={label} className="flex flex-1 flex-col items-center gap-2">
              <div className="flex h-28 w-full items-end">
                <div
                  className="w-full rounded-t-md bg-teal-400/70"
                  style={{ height: `${(count / funnelMax) * 100}%` }}
                />
              </div>
              <p className="font-mono text-lg text-paper">{count}</p>
              <p className="text-center text-xs text-muted">{label}</p>
            </div>
          ))}
        </div>
      </section>

      <p className="rounded-xl border border-dashed border-line px-4 py-3 text-xs text-muted">
        La facturation Stripe (report d&apos;usage automatique) est un scaffold prêt côté serveur
        (<code className="font-mono">billing.py</code>) mais désactivée tant qu&apos;aucun
        abonnement n&apos;est réglé pour ce tenant — rien à afficher ici tant que ce n&apos;est pas
        le cas, en cohérence avec la contrainte 0€ du prototype.
      </p>
    </div>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-ink-850 p-4">
      <p className="mb-1 text-xs uppercase tracking-wide text-muted">{label}</p>
      <p className="font-display text-2xl font-semibold text-paper">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-3 font-mono text-xs uppercase tracking-[0.14em] text-muted">{children}</h2>;
}
