import { NavRail } from "@/components/nav-rail";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <NavRail />
      <div className="flex-1 overflow-x-hidden">{children}</div>
    </div>
  );
}
