"use client";
import {
  Activity, Bot, Cloud, Cpu, FolderOpen, HardDrive, LayoutDashboard, ListChecks, LogOut, Menu as MenuIcon, Moon,
  RefreshCw, Search, Settings, Shield, Smartphone, Sun, WifiOff, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, post } from "@/lib/api";
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

export default function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const me = useMe();
  const { online } = useServerOnline();
  const { theme, toggle } = useTheme();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const pending = useQuery({
    queryKey: ["approvals-count"],
    queryFn: () => api<unknown[]>("/api/ai/approvals"),
    enabled: me.data?.user.role === "admin",
    refetchInterval: 20_000,
  });

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (me.data && !me.data.setup_complete && me.data.user.role === "admin" && pathname !== "/setup") router.replace("/setup");
  }, [me.data, pathname, router]);

  const focusSearch = useCallback((e: KeyboardEvent) => {
    e.preventDefault();
    document.getElementById("global-search")?.focus();
  }, []);
  useHotkey("k", focusSearch, { ctrl: true });
  useHotkey("/", focusSearch);

  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;
  useEffect(() => {
    if (unauthenticated) location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }, [unauthenticated]);

  if (me.isLoading || unauthenticated) return <div className="flex h-screen items-center justify-center"><Spinner className="h-6 w-6" /></div>;
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
  const user = me.data.user;
  const nav = NAV.filter((n) => user.role === "admin" || !["/security", "/remote", "/sync"].includes(n.href));
  const approvals = pending.data?.length || 0;

  const logout = async () => {
    await post("/api/auth/logout").catch(() => undefined);
    location.href = "/login";
  };

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className={cn("fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r bg-sidebar transition-transform lg:translate-x-0",
        open ? "translate-x-0" : "-translate-x-full")}>
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground"><Cloud className="h-4 w-4" /></div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{me.data.server_name}</div>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={cn("h-1.5 w-1.5 rounded-full", online ? "bg-success" : "bg-danger")} />
              {online ? "Online" : "Unreachable"}
            </div>
          </div>
          <button className="rounded p-1 hover:bg-muted lg:hidden" onClick={() => setOpen(false)} aria-label="Close menu"><X className="h-4 w-4" /></button>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {nav.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link key={href} href={href}
                className={cn("flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                  active ? "bg-card font-medium text-foreground shadow-sm ring-1 ring-border" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
                <Icon className="h-4 w-4" />
                <span className="flex-1">{label}</span>
                {href === "/ai" && approvals > 0 && <Badge tone="warning">{approvals}</Badge>}
              </Link>
            );
          })}
        </nav>
        <div className="border-t p-3 text-xs text-muted-foreground">Data stays on this PC. Access is private to your devices.</div>
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
              <button className="flex h-8 w-8 items-center justify-center rounded-full bg-muted text-sm font-semibold uppercase" aria-label="Account menu">{user.username[0]}</button>
            </MenuTrigger>
            <MenuContent>
              <div className="px-2 py-1.5 text-sm"><div className="font-medium">{user.username}</div><div className="text-xs capitalize text-muted-foreground">{user.role}</div></div>
              <MenuSeparator />
              <MenuItem icon={<Shield className="h-4 w-4" />} onSelect={() => router.push("/security")}>Security & sessions</MenuItem>
              <MenuItem icon={<LogOut className="h-4 w-4" />} onSelect={logout}>Sign out</MenuItem>
            </MenuContent>
          </Menu>
        </header>
        {!online && (
          <div className="flex items-center gap-2 border-b border-danger/30 bg-danger/10 px-4 py-2 text-sm text-danger">
            <WifiOff className="h-4 w-4" /> Lost connection to your home PC. It may be off or asleep — changes cannot be saved until it is back.
          </div>
        )}
        <main className="mx-auto w-full max-w-[1400px] flex-1 p-4 sm:p-6">{children}</main>
      </div>
      <UploadTray />
    </div>
  );
}
