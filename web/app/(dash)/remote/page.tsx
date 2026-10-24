"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Monitor, Play, Plus, Power, Square, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, del, post, put } from "@/lib/api";
import { bytes, date, duration, pct, rate } from "@/lib/format";
import { Sparkline } from "@/components/charts";
import {
  Badge, Button, Card, CardBody, CardHeader, Code, Dialog, ErrorBox, Field, Input, Loading, Notice, PageHeader, Progress, Select, Switch,
  Table, Td, Th, useConfirm, useToast,
} from "@/components/ui";

type Metrics = {
  hostname: string; boot_time: number; agent_started: number;
  cpu: { percent: number; cores: number; threads: number; freq_mhz: number | null; per_core: number[] };
  memory: { total: number; used: number; available: number; percent: number };
  network: { up_bps: number; down_bps: number }; disk_io: { read_bps: number; write_bps: number };
  disks: { mount: string; fstype: string; total: number; used: number; free: number; percent: number }[];
  gpus: { name: string; utilization: number; memory_used_mb: number; memory_total_mb: number; temperature_c: number }[];
  temperatures: { sensor: string; label: string; current: number }[]; battery: { percent: number; plugged: boolean } | null;
  history: { t: number; cpu: number; mem: number; net_up: number; net_down: number; disk_read: number; disk_write: number }[];
};
type App = { id: number; name: string; kind: "app" | "script"; executable: string; args: string[]; allow_ai: number; timeout_seconds: number; status: { running: boolean; pid?: number } | null };
type Rd = { config: { provider: string; rustdesk_id: string; rdp_host: string; notes: string }; rustdesk: { installed: boolean; running: boolean }; rdp: { enabled: boolean | null; listening: boolean; windows_edition: string | null; supported: boolean } };
type Errors = { recent: { ts: number; actor: string; action: string; target: string; status: string }[]; failed_jobs: { id: number; title: string; message: string; finished_at: number }[] };

export default function RemotePage() {
  const m = useQuery({ queryKey: ["metrics"], queryFn: () => api<Metrics>("/api/system/metrics"), refetchInterval: 2000 });
  const now = Date.now() / 1000;
  return (
    <>
      <PageHeader title="Remote Computer" description={m.data ? `${m.data.hostname} · Windows up ${duration(now - m.data.boot_time)} · agent up ${duration(now - m.data.agent_started)}` : "Live system status"} />
      {m.isLoading ? <Loading /> : m.error ? <ErrorBox error={m.error} /> : m.data && <MetricsView m={m.data} />}
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <RemoteDesktop />
        <ErrorsCard />
      </div>
      <AppsCard />
    </>
  );
}

function MetricsView({ m }: { m: Metrics }) {
  const h = m.history;
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Card className="p-4">
        <div className="text-xs font-medium text-muted-foreground">CPU · {m.cpu.cores} cores / {m.cpu.threads} threads{m.cpu.freq_mhz ? ` · ${(m.cpu.freq_mhz / 1000).toFixed(1)} GHz` : ""}</div>
        <div className="mt-1 text-2xl font-semibold tabular">{Math.round(m.cpu.percent)}%</div>
        <Sparkline values={h.map((x) => x.cpu)} max={100} />
        <div className="mt-2 flex h-8 items-end gap-0.5">{m.cpu.per_core.map((c, i) => <div key={i} className="flex-1 rounded-sm bg-accent/70" style={{ height: `${Math.max(4, c)}%` }} title={`Core ${i}: ${c}%`} />)}</div>
      </Card>
      <Card className="p-4">
        <div className="text-xs font-medium text-muted-foreground">Memory</div>
        <div className="mt-1 text-2xl font-semibold tabular">{Math.round(m.memory.percent)}%</div>
        <Sparkline values={h.map((x) => x.mem)} max={100} tone="warning" />
        <div className="text-xs text-muted-foreground">{bytes(m.memory.used)} used · {bytes(m.memory.available)} available</div>
      </Card>
      <Card className="p-4">
        <div className="text-xs font-medium text-muted-foreground">Network</div>
        <div className="mt-1 text-sm tabular">↑ {rate(m.network.up_bps)} · ↓ {rate(m.network.down_bps)}</div>
        <Sparkline values={h.map((x) => x.net_up + x.net_down)} tone="success" />
        <div className="text-xs text-muted-foreground">Disk: read {rate(m.disk_io.read_bps)} · write {rate(m.disk_io.write_bps)}</div>
      </Card>
      <Card className="p-4">
        <div className="text-xs font-medium text-muted-foreground">GPU & sensors</div>
        {m.gpus.length ? m.gpus.map((g) => (
          <div key={g.name} className="mt-1 text-sm"><div className="truncate">{g.name}</div><div className="text-xs text-muted-foreground">{g.utilization}% · {g.memory_used_mb}/{g.memory_total_mb} MB · {g.temperature_c}°C</div><Progress className="mt-1" value={g.utilization} /></div>
        )) : <p className="mt-1 text-sm text-muted-foreground">No NVIDIA GPU detected (requires nvidia-smi).</p>}
        {m.temperatures.slice(0, 3).map((t) => <div key={t.sensor + t.label} className="text-xs">{t.label || t.sensor}: {t.current}°C</div>)}
        {!m.temperatures.length && <p className="mt-2 text-xs text-muted-foreground">CPU temperature is not exposed by Windows without vendor tools.</p>}
        {m.battery && <div className="mt-2 text-xs">Battery {m.battery.percent}% {m.battery.plugged ? "(plugged in)" : "(on battery)"}</div>}
      </Card>
      <Card className="md:col-span-2 xl:col-span-4">
        <CardHeader title="Disks" />
        <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {m.disks.map((d) => (
            <div key={d.mount} className="text-sm">
              <div className="mb-1 flex justify-between"><span className="font-medium">{d.mount} <span className="text-xs text-muted-foreground">{d.fstype}</span></span><span className="text-muted-foreground">{d.percent}%</span></div>
              <Progress value={d.percent} tone={d.percent > 90 ? "danger" : d.percent > 80 ? "warning" : "accent"} />
              <div className="mt-1 text-xs text-muted-foreground">{bytes(d.free)} free of {bytes(d.total)}</div>
            </div>
          ))}
        </CardBody>
      </Card>
    </div>
  );
}

function RemoteDesktop() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["rd"], queryFn: () => api<Rd>("/api/remote-desktop"), refetchInterval: 15000 });
  const [cfg, setCfg] = useState<Rd["config"] | null>(null);
  useEffect(() => { if (q.data && !cfg) setCfg(q.data.config); }, [q.data, cfg]);
  if (!q.data || !cfg) return <Card><Loading /></Card>;
  const rd = q.data;
  const save = async () => {
    try { await put("/api/remote-desktop", cfg); toast("success", "Remote desktop settings saved"); qc.invalidateQueries({ queryKey: ["rd"] }); }
    catch (e) { toast("error", (e as Error).message); }
  };
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Monitor className="h-4 w-4" />Remote desktop</span>}
        description="Uses a trusted remote-access tool. The full desktop is never streamed through this web app." />
      <CardBody className="space-y-4 text-sm">
        <div className="flex flex-wrap gap-2">
          <Badge tone={rd.rustdesk.running ? "success" : rd.rustdesk.installed ? "warning" : "neutral"}>RustDesk: {rd.rustdesk.running ? "running" : rd.rustdesk.installed ? "installed, not running" : "not installed"}</Badge>
          <Badge tone={rd.rdp.enabled ? "success" : "neutral"}>RDP: {rd.rdp.enabled ? (rd.rdp.listening ? "enabled & listening" : "enabled") : rd.rdp.supported ? "disabled" : `not available on ${rd.rdp.windows_edition || "this edition"}`}</Badge>
        </div>
        <Field label="Provider">
          <Select className="w-full" value={cfg.provider} onChange={(e) => setCfg({ ...cfg, provider: e.target.value })}>
            <option value="none">Not configured</option><option value="rustdesk">RustDesk (recommended, works on Windows Home)</option>
            <option value="rdp">Microsoft Remote Desktop (Windows Pro)</option><option value="vnc">VNC</option>
          </Select>
        </Field>
        {cfg.provider === "rustdesk" && <Field label="RustDesk ID" hint="Shown in RustDesk on this PC. Set a permanent password in RustDesk → Security."><Input value={cfg.rustdesk_id} onChange={(e) => setCfg({ ...cfg, rustdesk_id: e.target.value })} /></Field>}
        {(cfg.provider === "rdp" || cfg.provider === "vnc") && <Field label="Host (Tailscale name)" hint="Keep port 3389/5900 closed on your router; connect only over Tailscale."><Input placeholder="my-pc.tail1234.ts.net" value={cfg.rdp_host} onChange={(e) => setCfg({ ...cfg, rdp_host: e.target.value })} /></Field>}
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={save}>Save</Button>
          {cfg.provider === "rustdesk" && cfg.rustdesk_id && <a href={`rustdesk://${cfg.rustdesk_id.replace(/\s/g, "")}`}><Button>Open in RustDesk</Button></a>}
          {cfg.provider === "rdp" && <a href="/api/remote-desktop/connection.rdp"><Button>Download .rdp connection</Button></a>}
        </div>
        <Notice>Opening a session requires the remote-desktop app on the device you are using, signed in to Tailscale. Windows sign-in is still required inside the session.</Notice>
      </CardBody>
    </Card>
  );
}

function ErrorsCard() {
  const q = useQuery({ queryKey: ["errors"], queryFn: () => api<Errors>("/api/system/errors"), refetchInterval: 30000 });
  return (
    <Card>
      <CardHeader title="Recent errors" description="Failed sign-ins, denied requests and failed tasks." />
      <CardBody className="max-h-80 overflow-y-auto text-sm">
        {q.isLoading ? <Loading /> : !q.data?.recent.length && !q.data?.failed_jobs.length ? <p className="text-muted-foreground">No recent errors.</p> : (
          <ul className="space-y-2">
            {q.data?.failed_jobs.map((j) => <li key={`j${j.id}`} className="flex gap-2"><Badge tone="danger">task</Badge><span className="flex-1">{j.title}: {j.message}</span><span className="text-xs text-muted-foreground">{date(j.finished_at)}</span></li>)}
            {q.data?.recent.map((r, i) => <li key={i} className="flex gap-2"><Badge tone="warning">{r.status}</Badge><span className="flex-1 truncate">{r.action} {r.target} <span className="text-muted-foreground">({r.actor})</span></span><span className="text-xs text-muted-foreground">{date(r.ts)}</span></li>)}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function AppsCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, node] = useConfirm();
  const q = useQuery({ queryKey: ["apps"], queryFn: () => api<App[]>("/api/apps"), refetchInterval: 10000 });
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: "", kind: "script" as "app" | "script", executable: "", args: "", allow_ai: false, timeout_seconds: 3600 });
  const [error, setError] = useState<unknown>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["apps"] });
  const add = async () => {
    setError(null);
    try {
      await post("/api/apps", { ...form, args: form.args.trim() ? form.args.match(/(?:[^\s"]+|"[^"]*")+/g)?.map((a) => a.replace(/^"|"$/g, "")) : [] });
      setAdding(false); refresh();
    } catch (e) { setError(e); }
  };
  const act = async (a: App, action: "start" | "stop") => {
    try {
      const r = await post<{ job_id?: number }>(`/api/apps/${a.id}/${action}`);
      toast("success", r.job_id ? `Script started (task #${r.job_id})` : action === "start" ? `${a.name} started` : `${a.name} stopped`);
      refresh();
    } catch (e) { toast("error", (e as Error).message); }
  };
  const remove = async (a: App) => { if (await confirm(`Remove “${a.name}” from approved list?`, { danger: true, confirm: "Remove" })) { await del(`/api/apps/${a.id}`); refresh(); } };
  return (
    <Card className="mt-4">
      {node}
      <CardHeader title="Approved applications & scripts" description="Only programs registered here can be started remotely. There is no free-form command line, PowerShell or shell access."
        action={<Button size="sm" variant="outline" onClick={() => setAdding(true)}><Plus className="h-4 w-4" />Register</Button>} />
      {q.isLoading ? <Loading /> : !q.data?.length ? <CardBody><p className="text-sm text-muted-foreground">Nothing registered. Scripts must live in the server’s scripts folder (shown in Settings → Diagnostics).</p></CardBody> : (
        <Table><thead><tr><Th>Name</Th><Th>Type</Th><Th>Program</Th><Th>Status</Th><Th /></tr></thead><tbody>
          {q.data.map((a) => (
            <tr key={a.id}>
              <Td>{a.name}{a.allow_ai ? <Badge tone="accent" className="ml-2">AI (with approval)</Badge> : null}</Td>
              <Td><Badge>{a.kind}</Badge></Td>
              <Td className="max-w-xs truncate font-mono text-xs">{a.executable} {a.args.join(" ")}</Td>
              <Td>{a.kind === "app" ? <Badge tone={a.status?.running ? "success" : "neutral"}>{a.status?.running ? `running (pid ${a.status.pid})` : "stopped"}</Badge> : `timeout ${a.timeout_seconds}s`}</Td>
              <Td className="whitespace-nowrap text-right">
                {a.kind === "app" && a.status?.running ? <Button size="sm" variant="outline" onClick={() => act(a, "stop")}><Square className="h-4 w-4" />Stop</Button>
                  : <Button size="sm" variant="outline" onClick={() => act(a, "start")}>{a.kind === "app" ? <Power className="h-4 w-4" /> : <Play className="h-4 w-4" />}{a.kind === "app" ? "Start" : "Run"}</Button>}
                <Button size="sm" variant="ghost" onClick={() => remove(a)} aria-label="Remove"><Trash2 className="h-4 w-4" /></Button>
              </Td>
            </tr>
          ))}
        </tbody></Table>
      )}
      <Dialog open={adding} onOpenChange={setAdding} title="Register approved program"
        footer={<><Button variant="outline" onClick={() => setAdding(false)}>Cancel</Button><Button onClick={add} disabled={!form.name || !form.executable}>Register</Button></>}>
        <div className="space-y-3">
          <Field label="Type"><Select className="w-full" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as "app" | "script" })}><option value="script">Script (runs as a background task, output logged)</option><option value="app">Application / service (start & stop)</option></Select></Field>
          <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Executable path" hint={form.kind === "script" ? <>.ps1 / .py / .bat / .exe placed in the server scripts folder.</> : "Full path to the .exe."}><Input value={form.executable} onChange={(e) => setForm({ ...form, executable: e.target.value })} placeholder={form.kind === "script" ? "…\\PersonalCloudOS\\scripts\\cleanup.ps1" : "C:\\Program Files\\Plex\\Plex Media Server.exe"} /></Field>
          <Field label="Fixed arguments (optional)" hint={<>Arguments are fixed at registration; nobody can pass new ones later. Example: <Code>-Days 30</Code></>}><Input value={form.args} onChange={(e) => setForm({ ...form, args: e.target.value })} /></Field>
          {form.kind === "script" && <>
            <Field label="Time limit (seconds)"><Input type="number" value={form.timeout_seconds} onChange={(e) => setForm({ ...form, timeout_seconds: Number(e.target.value) })} /></Field>
            <Switch checked={form.allow_ai} onCheckedChange={(v) => setForm({ ...form, allow_ai: v })} label="AI may request this script" description="Each run still needs your approval." />
          </>}
          <ErrorBox error={error} />
        </div>
      </Dialog>
    </Card>
  );
}
void pct;
