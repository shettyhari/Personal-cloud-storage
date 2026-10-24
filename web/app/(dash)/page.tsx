"use client";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Bot, Cpu, HardDrive, MemoryStick, RefreshCw, ShieldCheck, Smartphone, Upload } from "lucide-react";
import Link from "next/link";
import { api, type FileEntry, type Job } from "@/lib/api";
import { ago, bytes, date, duration, pct, rate } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { Sparkline } from "@/components/charts";
import { FileIcon, kindOf } from "@/components/file-icon";
import { Badge, Card, CardBody, CardHeader, Empty, ErrorBox, Loading, PageHeader, Progress, Stat, StatusDot } from "@/components/ui";

type Overview = {
  server_name: string; online: boolean; uptime_seconds: number; agent_uptime_seconds: number; cpu: number;
  memory: { total: number; used: number; percent: number }; network: { up_bps: number; down_bps: number };
  storage: { root: string; drive: string; total: number; used: number; free: number }[];
  recent_files: FileEntry[]; active_jobs: Job[]; active_uploads: { id: string; filename: string; size: number; received: number }[];
  connected_devices: { id: number; name: string; kind: string; last_used: number; last_ip: string }[];
  active_sessions: number; ai_activity: { ts: number; actor: string; action: string; target: string; status: string }[];
  pending_approvals: number; sync: { id: number; name: string; last_run: number; last_status: string; last_error: string }[];
  backups: { id: number; name: string; last_run: number; last_status: string; last_error: string }[];
  history: { t: number; cpu: number; mem: number; net_up: number; net_down: number }[];
};

export default function OverviewPage() {
  const me = useMe();
  const q = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/system/overview"), refetchInterval: 5000 });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const o = q.data;
  const drives = Object.values(Object.fromEntries(o.storage.map((s) => [s.drive, s])));
  const total = drives.reduce((a, d) => a + d.total, 0), used = drives.reduce((a, d) => a + d.used, 0);
  const memPct = Math.round(o.memory.percent);

  return (
    <>
      <PageHeader title="Overview" description={<span className="flex items-center gap-2"><StatusDot ok /> {o.server_name} is online · up {duration(o.uptime_seconds)} · agent running {duration(o.agent_uptime_seconds)}</span>} />
      {o.pending_approvals > 0 && (
        <Link href="/ai" className="mb-4 flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
          <Bot className="h-4 w-4 text-warning" /> <b>{o.pending_approvals}</b> AI action{o.pending_approvals > 1 ? "s" : ""} waiting for your approval →
        </Link>
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Storage used" icon={<HardDrive className="h-4 w-4" />} value={total ? `${pct(used, total)}%` : "—"}
          sub={total ? `${bytes(used)} of ${bytes(total)} · ${bytes(total - used)} free` : "No storage folders yet"} />
        <Card className="p-4">
          <div className="flex items-center justify-between text-xs font-medium text-muted-foreground">CPU<Cpu className="h-4 w-4" /></div>
          <div className="mt-2 text-2xl font-semibold tabular">{Math.round(o.cpu)}%</div>
          <Sparkline values={o.history.map((h) => h.cpu)} max={100} className="mt-1 h-10" />
        </Card>
        <Card className="p-4">
          <div className="flex items-center justify-between text-xs font-medium text-muted-foreground">Memory<MemoryStick className="h-4 w-4" /></div>
          <div className="mt-2 text-2xl font-semibold tabular">{memPct}%</div>
          <div className="text-xs text-muted-foreground">{bytes(o.memory.used)} of {bytes(o.memory.total)}</div>
          <Progress value={memPct} className="mt-2" tone={memPct > 90 ? "danger" : memPct > 75 ? "warning" : "accent"} />
        </Card>
        <Card className="p-4">
          <div className="text-xs font-medium text-muted-foreground">Network</div>
          <div className="mt-2 flex gap-4 text-sm tabular">
            <span className="flex items-center gap-1"><ArrowUp className="h-4 w-4 text-accent" />{rate(o.network.up_bps)}</span>
            <span className="flex items-center gap-1"><ArrowDown className="h-4 w-4 text-success" />{rate(o.network.down_bps)}</span>
          </div>
          <Sparkline values={o.history.map((h) => h.net_down + h.net_up)} className="mt-1 h-10" tone="success" />
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Recent files" action={<Link className="text-sm text-accent" href="/files">Open files</Link>} />
          <CardBody className="pt-3">
            {o.recent_files.length ? (
              <ul className="divide-y">
                {o.recent_files.map((f) => {
                  const root = me.data?.roots.find((r) => r.id === f.root_id);
                  const dir = f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "";
                  return (
                    <li key={`${f.root_id}:${f.path}`}>
                      <Link href={`/files?root=${f.root_id}&path=${encodeURIComponent(dir)}`} className="flex items-center gap-3 py-2 text-sm hover:text-accent">
                        <FileIcon kind={kindOf(f.name)} />
                        <span className="min-w-0 flex-1 truncate">{f.name}<span className="ml-2 text-xs text-muted-foreground">{root?.name}/{dir}</span></span>
                        <span className="shrink-0 text-xs text-muted-foreground">{bytes(f.size)} · {date(f.modified)}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            ) : <Empty title="No files indexed yet" description="Add a storage folder and upload files — indexing runs in the background." />}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Storage" action={<Link className="text-sm text-accent" href="/storage">Details</Link>} />
          <CardBody className="space-y-4 pt-3">
            {drives.length ? drives.map((d) => (
              <div key={d.drive} className="text-sm">
                <div className="mb-1 flex justify-between"><span className="font-medium">Drive {d.drive}</span><span className="text-muted-foreground tabular">{bytes(d.free)} free</span></div>
                <Progress value={pct(d.used, d.total)} tone={pct(d.used, d.total) > 90 ? "danger" : pct(d.used, d.total) > 80 ? "warning" : "accent"} />
              </div>
            )) : <Empty title="No storage folders" action={<Link className="text-sm text-accent" href="/storage">Add a folder</Link>} />}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Active transfers & jobs" />
          <CardBody className="space-y-3 pt-3">
            {o.active_uploads.map((u) => (
              <div key={u.id} className="text-sm"><div className="flex items-center gap-2"><Upload className="h-4 w-4 text-accent" /><span className="flex-1 truncate">{u.filename}</span><span className="text-xs text-muted-foreground">{bytes(u.received)} / {bytes(u.size)}</span></div><Progress className="mt-1 h-1" value={pct(u.received, u.size)} /></div>
            ))}
            {o.active_jobs.map((j) => (
              <div key={j.id} className="text-sm"><div className="flex items-center gap-2"><span className="flex-1 truncate">{j.title}</span><Badge tone="accent">{j.status}</Badge></div><div className="truncate text-xs text-muted-foreground">{j.message}</div><Progress className="mt-1 h-1" value={j.progress * 100} /></div>
            ))}
            {!o.active_uploads.length && !o.active_jobs.length && <p className="text-sm text-muted-foreground">Nothing running right now.</p>}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Connected devices" action={<Link className="text-sm text-accent" href="/mobile">Manage</Link>} />
          <CardBody className="pt-3">
            <p className="mb-2 text-sm text-muted-foreground">{o.active_sessions} active browser session{o.active_sessions === 1 ? "" : "s"}</p>
            {o.connected_devices.length ? (
              <ul className="space-y-2">
                {o.connected_devices.map((d) => (
                  <li key={d.id} className="flex items-center gap-2 text-sm">{d.kind === "mcp" ? <Bot className="h-4 w-4 text-accent" /> : <Smartphone className="h-4 w-4 text-muted-foreground" />}<span className="flex-1 truncate">{d.name}</span><span className="text-xs text-muted-foreground">{ago(d.last_used)}</span></li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted-foreground">No devices active in the last 24 hours.</p>}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Backup & sync health" action={<Link className="text-sm text-accent" href="/sync">Open</Link>} />
          <CardBody className="space-y-2 pt-3 text-sm">
            {[...o.backups.map((b) => ({ ...b, kind: "Backup" })), ...o.sync.map((s) => ({ ...s, kind: "Sync" }))].map((x) => (
              <div key={x.kind + x.id} className="flex items-center gap-2">
                {x.kind === "Backup" ? <ShieldCheck className="h-4 w-4 text-muted-foreground" /> : <RefreshCw className="h-4 w-4 text-muted-foreground" />}
                <span className="flex-1 truncate">{x.name}</span>
                <Badge tone={x.last_status === "ok" ? "success" : x.last_status === "error" ? "danger" : x.last_status ? "warning" : "neutral"}>{x.last_status || "never run"}</Badge>
                <span className="w-16 text-right text-xs text-muted-foreground">{ago(x.last_run)}</span>
              </div>
            ))}
            {!o.backups.length && <p className="text-warning">No backup configured — your files have no recovery point.</p>}
          </CardBody>
        </Card>

        <Card className="lg:col-span-3">
          <CardHeader title="Recent AI activity" action={<Link className="text-sm text-accent" href="/ai">AI Workspace</Link>} />
          <CardBody className="pt-3">
            {o.ai_activity.length ? (
              <ul className="divide-y text-sm">
                {o.ai_activity.map((a, i) => (
                  <li key={i} className="flex items-center gap-3 py-2"><Bot className="h-4 w-4 text-accent" /><span className="font-mono text-xs">{a.action}</span><span className="min-w-0 flex-1 truncate text-muted-foreground">{a.target}</span><Badge tone={a.status === "ok" ? "success" : a.status === "pending" ? "warning" : "danger"}>{a.status}</Badge><span className="text-xs text-muted-foreground">{ago(a.ts)}</span></li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted-foreground">No AI assistant has accessed your files yet.</p>}
          </CardBody>
        </Card>
      </div>
    </>
  );
}
