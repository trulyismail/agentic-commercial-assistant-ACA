"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { logout } from "@/app/logout/actions";

const ITEMS = [
  { href: "/", label: "Vue d'ensemble", icon: RadarIcon },
  { href: "/settings", label: "Réglages", icon: SlidersIcon },
  { href: "/billing", label: "Facturation", icon: GaugeIcon },
];

export function NavRail() {
  const pathname = usePathname();

  return (
    <nav className="flex w-[76px] shrink-0 flex-col items-center border-r border-line bg-ink-900/60 py-5">
      <div className="mb-8 flex h-10 w-10 items-center justify-center rounded-xl border border-teal-400/40 bg-teal-400/10">
        <span className="font-display text-sm font-semibold text-teal-300">A</span>
      </div>

      <ul className="flex flex-1 flex-col items-center gap-2">
        {ITEMS.map((item) => {
          const active = pathname === item.href;
          const Icon = item.icon;
          return (
            <li key={item.href} className="group relative">
              <Link
                href={item.href}
                className={`flex h-11 w-11 items-center justify-center rounded-xl transition-colors ${
                  active
                    ? "bg-teal-400/15 text-teal-300"
                    : "text-muted hover:bg-ink-800 hover:text-paper-dim"
                }`}
                aria-current={active ? "page" : undefined}
              >
                <Icon />
              </Link>
              <span className="pointer-events-none absolute left-full top-1/2 z-20 ml-2 -translate-y-1/2 whitespace-nowrap rounded-md border border-line bg-ink-850 px-2 py-1 text-xs text-paper-dim opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
                {item.label}
              </span>
            </li>
          );
        })}
      </ul>

      <form action={logout}>
        <button
          type="submit"
          title="Se déconnecter"
          className="flex h-10 w-10 items-center justify-center rounded-xl text-muted transition-colors hover:bg-rose-400/10 hover:text-rose-300"
        >
          <LogoutIcon />
        </button>
      </form>
    </nav>
  );
}

function RadarIcon() {
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 12 L18 7" strokeLinecap="round" />
      <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
    </svg>
  );
}
function SlidersIcon() {
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M4 6h10M18 6h2M4 12h2M8 12h12M4 18h14M20 18h0" strokeLinecap="round" />
      <circle cx="16" cy="6" r="2" />
      <circle cx="6" cy="12" r="2" />
      <circle cx="18" cy="18" r="2" />
    </svg>
  );
}
function GaugeIcon() {
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M4 15a8 8 0 1 1 16 0" />
      <path d="M12 15 L16 10" strokeLinecap="round" />
    </svg>
  );
}
function LogoutIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M16 17l5-5-5-5M21 12H9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
