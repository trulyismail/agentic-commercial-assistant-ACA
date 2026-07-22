import { PulseGraph } from "@/components/pulse-graph";
import { LoginForm } from "./login-form";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden px-6">
      <div className="pointer-events-none absolute inset-0 opacity-70">
        <PulseGraph mode="ambient" className="h-full w-full" />
      </div>
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-ink-950 via-ink-950/70 to-ink-950" />

      <div className="relative z-10 flex w-full max-w-sm flex-col items-center text-center">
        <div
          className="mb-8 flex flex-col items-center gap-3"
          style={{ animation: "fade-up 0.6s cubic-bezier(0.22,1,0.36,1) both" }}
        >
          <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-teal-400/40 bg-teal-400/10">
            <span className="font-display text-lg font-semibold text-teal-300">A</span>
          </div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-paper">
            Cockpit ACA
          </h1>
          <p className="text-sm text-muted">
            Votre équipe d&apos;agents traite les leads. Vous gardez la main.
          </p>
        </div>

        <LoginForm next={next ?? "/"} />
      </div>

      <style>{`
        @keyframes fade-up {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </main>
  );
}
