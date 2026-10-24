"use client";
import {
  Activity, Bot, Check, ChevronsUpDown, Cloud, CloudUpload, Cpu, FolderOpen, HardDrive, LayoutDashboard, ListChecks,
  LogOut, Menu as MenuIcon, Moon, RefreshCw, Search, Server as ServerIcon, Settings, Shield, Smartphone, Sun, WifiOff, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, post } from "@/lib/api";
import { CLOUD, isOnline, setServerId } from "@/lib/cloud";
import { cloudSignOut, useCloudSession, useSelectedServer } from "@/lib/cloud-hooks";
import { cn } from "@/lib/format";
import { useHotkey, useMe, useServerOnline, useTheme } from "@/lib/hooks";
import { Badge, Button, Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger, Spinner } from "./ui";
import { UploadTray } from "./upload-tray";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/files", label: "My Files", icon: FolderOpen },
  { href: "/storage", label: "Drives & Storage", icon: HardDrive },
  { href: "/mobile", label: "Connect Phone", icon: Smartphone },
  { href: "/ai", label: "AI Workspace", icon: Bot },
  { href: "/remote", label: "Remote Computer", icon: Cpu },
  { href: "/tasks", label: "Background Tasks", icon: ListChecks },
  { href: "/sync", label: "Sync & Backup", icon: RefreshCw },
  { href: "/activity", label: "Activity Logs", icon: Activity },
  { href: "/security", label: "Security", icon: Shield },
  { href: "/settings", label: "Settings", icon: Settings },
];
const CLOUD_NAV = [
  { href: "/servers", label: "Servers", icon: ServerIcon },
  { href: "/cloud-files", label: "Cloud Storage", icon: CloudUpload },
];
// Hosted pages that do not need a connected server
const SERVERLESS = ["/servers", "/cloud-files"];

export default function Shell({ children }: { children: React.ReactNode }) {
  return CLOUD ? <CloudGate>{children}</CloudGate> : <LocalShell>{children}</LocalShell>;
}

// ----------------------------------------------------------------- local mode
function LocalShell({ children }: { children: React.ReactNode }) {
  const me = useMe();
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;
  useEffect(() => {
    if (unauthenticated) location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }, [unauthenticated]);
  if (me.isLoading || unauthenticated) return <FullSpinner />;
  if (!me.data) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 p-6 text-center">
        <WifiOff className="h-8 w-8 text-muted-foreground" />
        <div className="font-medium">Your home PC is not reachable</div>
        <p className="max-w-sm text-sm text-muted-foreground">The PC may be turned off, asleep, or disconnected from the internet / Tailscale. Files are only available while it is running.</p>
        <Button variant="outline" onClick={() => location.reload()}>Try again</Button>
      </div>
    );
  }
  return <Frame>{children}</Frame>;
}

// ----------------------------------------------------------------- cloud mode
function CloudGate({ children }: { children: React.ReactNode }) {
  const session = useCloudSession();
  const ok = session.data && session.data.aal === "aal2";
  useEffect(() => {
    if (session.isLoading) return;
    if (!ok) location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }, [session.isLoading, ok]);
  if (session.isLoading || !ok) return <FullSpinner />;
  return <Frame>{children}</Frame>;
}

function FullSpinner() {
  return <div className="flex h-screen items-center justify-center"><Spinner className="h-6 w-6" /></div>;
}

function ServerSwitcher() {
  const { servers, current } = useSelectedServer();
  const router = useRouter();
  const list = servers.data || [];
  return (
    <Menu>
      <MenuTrigger asChild>
        <button className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 text-left hover:bg-muted" aria-label="Switch server">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{current?.name || "No server yet"}</div>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={cn("h-1.5 w-1.5 rounded-full", isOnline(current) ? "bg-success" : "bg-danger")} />
              {current ? (isOnline(current) ? "Online" : "Offline") : "Add one on Servers"}
            </div>
          </div>
          <ChevronsUpDown className="h-4 w-4 text-muted-foreground" />
        </button>
      </MenuTrigger>
      <MenuContent align="start">
        {list.map((s) => (
          <MenuItem key={s.id} onSelect={() => setServerId(s.id)}
            icon={<span className={cn("h-2 w-2 rounded-full", isOnline(s) ? "bg-success" : "bg-muted-foreground")} />}>
            <span className="flex-1">{s.name}</span>{s.id === current?.id && <Check className="h-4 w-4" />}
          </MenuItem>
        ))}
        {!!list.length && <MenuSeparator />}
        <MenuItem icon={<ServerIcon className="h-4 w-4" />} onSelect={() => router.push("/servers")}>Manage servers</MenuItem>
      </MenuContent>
    </Menu>
  );
}

function ServerRequired() {
  const { current, servers } = useSelectedServer();
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border bg-card p-10 text-center">
      <WifiOff className="h-8 w-8 text-muted-foreground" />
      <div className="font-medium">{!servers.data?.length ? "No servers connected yet" : `${current?.name || "This server"} is offline`}</div>
      <p className="max-w-md text-sm text-muted-foreground">
        {!servers.data?.length ? "Add your home PC or a VPS on the Servers page; it connects out to this dashboard with a one-time link code."
          : "The computer may be off, asleep or without internet. It reconnects automatically when it is back."}
      </p>
      <Link href="/servers"><Button variant="outline">Open Servers</Button></Link>
    </div>
  );
}

// --------------------------------------------------------------- shared frame
function Frame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const me = useMe();
  const session = useCloudSession();
  const { online } = useServerOnline();
  const { theme, toggle } = useTheme();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const pending = useQuery({
    queryKey: ["approvals-count"],
    queryFn: () => api<unknown[]>("/api/ai/approvals"),
    enabled: me.data?.user.role === "admin" && (!CLOUD || online),
    refetchInterval: 20_000,
  });

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!CLOUD && me.data && !me.data.setup_complete && me.data.user.role === "admin" && pathname !== "/setup") router.replace("/setup");
  }, [me.data, pathname, router]);

  const focusSearch = useCallback((e: KeyboardEvent) => {
    e.preventDefault();
    document.getElementById("global-search")?.focus();
  }, []);
  useHotkey("k", focusSearch, { ctrl: true });
  useHotkey("/", focusSearch);

  const username = me.data?.user.username || session.data?.email || "?";
  const role = CLOUD ? "owner" : me.data?.user.role || "";
  const isAdmin = CLOUD || me.data?.user.role === "admin";
  const nav = [...(CLOUD ? CLOUD_NAV : []), ...NAV.filter((n) => (!CLOUD || n.href !== "/mobile")
    && (isAdmin || !["/security", "/remote", "/sync"].includes(n.href)))];
  const approvals = pending.data?.length || 0;
  const needsServer = CLOUD && !SERVERLESS.some((p) => pathname.startsWith(p));
  const blocked = needsServer && (!online || (!me.data && !me.isLoading));

  const logout = async () => {
    if (CLOUD) return cloudSignOut();
    await post("/api/auth/logout").catch(() => undefined);
    location.href = "/login";
  };

  return (
    <div className="flex min-h-screen">
      <aside className={cn("fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r bg-sidebar transition-transform lg:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full")}>
        <div className="flex h-14 items-center gap-2 border-b px-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground"><Cloud className="h-4 w-4" /></div>
          {CLOUD ? <ServerSwitcher /> : (
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">{me.data?.server_name}</div>
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span className={cn("h-1.5 w-1.5 rounded-full", online ? "bg-success" : "bg-danger")} />
                {online ? "Online" : "Unreachable"}
              </div>
            </div>
          )}
          <button className="rounded p-1 hover:bg-muted lg:hidden" onClick={() => setOpen(false)} aria-label="Close menu"><X className="h-4 w-4" /></button>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {nav.map(({ href, label, icon: Icon }, i) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <div key={href}>
                {CLOUD && i === CLOUD_NAV.length && <div className="mx-3 mb-1 mt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Selected server</div>}
                <Link href={href}
                  className={cn("flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                    active ? "bg-card font-medium text-foreground shadow-sm ring-1 ring-border" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
                  <Icon className="h-4 w-4" />
                  <span className="flex-1">{label}</span>
                  {href === "/ai" && approvals > 0 && <Badge tone="warning">{approvals}</Badge>}
                </Link>
              </div>
            );
          })}
        </nav>
        <div className="border-t p-3 text-xs text-muted-foreground">
          {CLOUD ? "Servers connect out to this dashboard; files stay on your machines." : "Data stays on this PC. Access is private to your devices."}
        </div>
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-black/30 lg:hidden" onClick={() => setOpen(false)} />}

      <div className="flex min-w-0 flex-1 flex-col lg:pl-64">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-background/90 px-4 backdrop-blur">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu"><MenuIcon className="h-5 w-5" /></Button>
          <form className="relative max-w-xl flex-1" onSubmit={(e) => { e.preventDefault(); router.push(`/files?q=${encodeURIComponent(search)}`); }}>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input id="global-search" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search files and document contents…"
              className="h-9 w-full rounded-md border border-input bg-card pl-9 pr-14 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
            <kbd className="pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 rounded border bg-muted px-1.5 text-[10px] text-muted-foreground sm:block">Ctrl K</kbd>
          </form>
          <div className="flex-1" />
          <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle theme">{theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}</Button>
          <Menu>
            <MenuTrigger asChild>
              <button className="flex h-8 w-8 items-center justify-center rounded-full bg-muted text-sm font-semibold uppercase" aria-label="Account menu">{username[0]}</button>
            </MenuTrigger>
            <MenuContent>
              <div className="px-2 py-1.5 text-sm"><div className="max-w-[220px] truncate font-medium">{CLOUD ? session.data?.email : username}</div><div className="text-xs capitalize text-muted-foreground">{role}</div></div>
              <MenuSeparator />
              {!CLOUD && <MenuItem icon={<Shield className="h-4 w-4" />} onSelect={() => router.push("/security")}>Security & sessions</MenuItem>}
              <MenuItem icon={<LogOut className="h-4 w-4" />} onSelect={logout}>Sign out</MenuItem>
            </MenuContent>
          </Menu>
        </header>
        {!CLOUD && !online && (
          <div className="flex items-center gap-2 border-b border-danger/30 bg-danger/10 px-4 py-2 text-sm text-danger">
            <WifiOff className="h-4 w-4" /> Lost connection to your home PC. It may be off or asleep — changes cannot be saved until it is back.
          </div>
        )}
        <main className="mx-auto w-full max-w-[1400px] flex-1 p-4 sm:p-6">
          {blocked ? <ServerRequired /> : needsServer && me.isLoading ? <div className="flex justify-center p-10"><Spinner className="h-6 w-6" /></div> : children}
        </main>
      </div>
      <UploadTray />
    </div>
  );
}
