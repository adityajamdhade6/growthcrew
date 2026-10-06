"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getToken, setToken, useApi } from "@/lib/api";
import { SessionContext, type Workspace } from "@/lib/session";
import { ErrorState, Loading } from "@/components/ui";
import { DemoBanner } from "@/components/demo";

const WORKSPACE_KEY = "growthcrew.workspace";

const NAV = [
  { href: "/", label: "Mission", icon: "M3 12h4l3-8 4 16 3-8h4" },
  { href: "/strategy", label: "Strategy", icon: "M4 19V5m0 14h16M8 15l3-4 3 2 5-7" },
  { href: "/calendar", label: "Calendar", icon: "M4 7h16v13H4zM4 11h16M8 4v4m8-4v4" },
  { href: "/results", label: "Results", icon: "M5 20V10m7 10V4m7 16v-7" },
  { href: "/signals", label: "Signals", icon: "M4 12a8 8 0 0 1 16 0M7.5 12a4.5 4.5 0 0 1 9 0M12 12v8" },
  { href: "/playbook", label: "Playbook", icon: "M5 4h11l3 3v13H5zM9 9h6M9 13h6M9 17h3" },
  { href: "/creative", label: "Creative", icon: "M4 5h16v14H4zM4 15l5-5 4 4 3-3 4 4" },
  { href: "/evals", label: "Evals", icon: "M9 12l2 2 4-4M5 4h14v16H5z" },
  { href: "/settings", label: "Settings", icon: "M4 7h10m4 0h2M4 17h4m4 0h8M14 5v4M8 15v4" },
];
// The phone bar shows these; the rest sit under More.
const PHONE = ["/", "/calendar", "/results", "/signals"];

function Icon({ path }: { path: string }) {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5 shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={path} />
    </svg>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [workspace, setWorkspaceState] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);

  useEffect(() => {
    if (!getToken()) router.replace("/login");
    else setReady(true);
  }, [router]);

  const me = useApi<{ email: string; is_admin: boolean; demo?: boolean }>(ready ? "/auth/me" : null);
  const list = useApi<Workspace[]>(ready ? "/workspaces" : null, 20000);

  useEffect(() => {
    if (!list.data) return;
    const saved = window.localStorage.getItem(WORKSPACE_KEY);
    const names = list.data.map((item) => item.workspace);
    if (workspace && names.includes(workspace)) return;
    if (saved && names.includes(saved)) setWorkspaceState(saved);
    else if (names.length) setWorkspaceState(names[0]);
    else if (pathname !== "/onboarding") router.replace("/onboarding");
  }, [list.data, workspace, pathname, router]);

  function setWorkspace(name: string) {
    window.localStorage.setItem(WORKSPACE_KEY, name);
    setWorkspaceState(name);
  }

  function signOut() {
    setToken(null);
    router.replace("/login");
  }

  if (me.error || list.error) {
    return (
      <main className="mx-auto max-w-md p-6">
        <ErrorState error={(me.error ?? list.error)!} retry={() => window.location.reload()} />
      </main>
    );
  }
  if (!me.data || !list.data) {
    return (
      <main className="mx-auto max-w-3xl p-6">
        <Loading />
      </main>
    );
  }

  const pending = list.data.find((item) => item.workspace === workspace)?.pending_approvals ?? 0;
  const switcher = (
    <select
      aria-label="Workspace"
      value={workspace}
      onChange={(event) =>
        event.target.value === "+new" ? router.push("/onboarding?new=1") : setWorkspace(event.target.value)
      }
      className="w-full min-w-0 truncate rounded-lg border border-line bg-surface px-2.5 py-2 text-sm font-medium"
    >
      {list.data.map((item) => (
        <option key={item.workspace} value={item.workspace}>
          {item.name}
        </option>
      ))}
      <option value="+new">+ Add a business</option>
    </select>
  );

  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <SessionContext.Provider
      value={{
        user: me.data,
        workspaces: list.data,
        workspace,
        setWorkspace,
        reloadWorkspaces: list.reload,
        signOut,
      }}
    >
      <div className="min-h-dvh md:flex">
        <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-surface p-4 md:flex">
          <p className="px-1 text-base font-semibold tracking-tight">GrowthCrew</p>
          <div className="mt-4">{switcher}</div>
          <nav className="mt-5 space-y-1" aria-label="Main">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive(item.href) ? "page" : undefined}
                className={`flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm font-medium ${
                  isActive(item.href) ? "bg-accent-soft text-accent" : "text-muted hover:bg-raised hover:text-ink"
                }`}
              >
                <Icon path={item.icon} />
                <span className="flex-1">{item.label}</span>
                {item.href === "/calendar" && pending > 0 && (
                  <span className="rounded-full bg-accent px-1.5 text-xs text-on-accent">{pending}</span>
                )}
              </Link>
            ))}
          </nav>
          <div className="mt-auto border-t border-line pt-3 text-sm">
            <p className="truncate text-muted">{me.data.email}</p>
            <button onClick={signOut} className="mt-1 font-medium text-muted hover:text-ink">
              Sign out
            </button>
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          {me.data.demo && <DemoBanner />}
          <div className="sticky top-0 z-20 flex items-center gap-3 border-b border-line bg-surface px-4 py-2.5 md:hidden">
            <span className="text-sm font-semibold">GrowthCrew</span>
            <div className="min-w-0 flex-1">{switcher}</div>
          </div>
          <main className="mx-auto max-w-6xl px-4 pb-28 pt-5 sm:px-6 md:pb-10 md:pt-8">
            {workspace || pathname === "/onboarding" ? children : <Loading />}
          </main>
        </div>

        {moreOpen && (
          <div className="fixed inset-x-0 bottom-14 z-30 border-t border-line bg-surface p-2 pb-[env(safe-area-inset-bottom)] md:hidden">
            {NAV.filter((item) => !PHONE.includes(item.href)).map((item) => (
              <Link key={item.href} href={item.href} onClick={() => setMoreOpen(false)} className="flex min-h-12 items-center gap-3 rounded-lg px-3 text-sm font-medium hover:bg-raised">
                <Icon path={item.icon} />
                {item.label}
              </Link>
            ))}
          </div>
        )}
        <nav
          aria-label="Main"
          className="fixed inset-x-0 bottom-0 z-30 flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden"
        >
          {NAV.filter((item) => PHONE.includes(item.href)).map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive(item.href) ? "page" : undefined}
              className={`relative flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${
                isActive(item.href) ? "text-accent" : "text-muted"
              }`}
            >
              <Icon path={item.icon} />
              {item.label}
              {item.href === "/calendar" && pending > 0 && (
                <span className="absolute right-[22%] top-1.5 rounded-full bg-accent px-1.5 text-[10px] text-on-accent">
                  {pending}
                </span>
              )}
            </Link>
          ))}
          <button
            onClick={() => setMoreOpen(!moreOpen)}
            aria-expanded={moreOpen}
            className="flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium text-muted"
          >
            <Icon path="M5 12h.01M12 12h.01M19 12h.01" />
            More
          </button>
        </nav>
      </div>
    </SessionContext.Provider>
  );
}
