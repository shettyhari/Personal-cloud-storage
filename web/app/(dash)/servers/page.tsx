"use client";
import { useQueryClient } from "@tanstack/react-query";
import { Laptop, Pencil, Plus, Server as ServerIcon, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { isOnline, makeLinkCode, sb, setServerId, type Server } from "@/lib/cloud";
import { useSelectedServer } from "@/lib/cloud-hooks";
import { ago, bytes, cn, duration, pct, rate } from "@/lib/format";
import {
  Badge, Button, Card, CopyBox, Dialog, Empty, ErrorBox, Field, Input, Loading, Notice, PageHeader, Progress, useConfirm, useToast,
} from "@/components/ui";

const REPO = "https://github.com/shettyhari/Personal-cloud-storage.git";

export default function ServersPage() {
  const { servers, current } = useSelectedServer();
  const router = useRouter();
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState<Server | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, node] = useConfirm();
  const list = servers.data || [];
  const online = list.filter(isOnline).length;

  const remove = async (s: Server) => {
    if (!(await confirm(`Remove “${s.name}”?`, { danger: true, confirm: "Remove",
      body: "It disconnects from this dashboard and its cloud account is deleted. Nothing on the computer itself is deleted; run `pcloud unlink` there to clear its saved link." }))) return;
    const { error } = await sb().functions.invoke("remove-server", { body: { server_id: s.id } });
    if (error) toast("error", error.message); else { toast("success", `${s.name} removed`); qc.invalidateQueries({ queryKey: ["servers"] }); }
  };

  return (
    <>
      {node}
      <PageHeader title="Servers" description={list.length ? `${online} of ${list.length} online. Pick one to operate it from this dashboard.` : "Connect your home PCs and VPS servers."}
        actions={<Button onClick={() => setAdding(true)}><Plus className="h-4 w-4" />Add server</Button>} />
      {servers.isLoading ? <Loading /> : servers.error ? <ErrorBox error={servers.error} /> : !list.length ? (
        <Card><Empty icon={<ServerIcon className="h-8 w-8" />} title="No servers yet"
          description="Install Personal Cloud on your home PC or a VPS, then link it here with a one-time code. It connects out to this dashboard — no router setup needed."
          action={<Button onClick={() => setAdding(true)}><Plus className="h-4 w-4" />Add your first server</Button>} /></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {list.map((s) => {
            const on = isOnline(s);
            const m = s.metrics || {};
            const disk = (m.disks || []).reduce((a, d) => ({ total: a.total + d.total, free: a.free + d.free }), { total: 0, free: 0 });
            return (
              <Card key={s.id} className={cn("flex flex-col p-4", s.id === current?.id && "ring-2 ring-accent")}>
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-muted">{s.kind === "linux_vps" ? <ServerIcon className="h-5 w-5" /> : <Laptop className="h-5 w-5" />}</div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2"><span className="truncate font-semibold">{s.name}</span><Badge tone={on ? "success" : "neutral"}>{on ? "online" : "offline"}</Badge></div>
                    <div className="truncate text-xs text-muted-foreground">{s.kind === "linux_vps" ? "Linux VPS" : "Windows PC"} · {s.hostname || "—"} · v{s.version || "?"}</div>
                    <div className="text-xs text-muted-foreground">{on ? `up ${m.boot_time ? duration(Date.now() / 1000 - m.boot_time) : "—"}` : `last seen ${s.last_seen ? ago(Date.parse(s.last_seen) / 1000) : "never"}`}</div>
                  </div>
                </div>
                {on && (
                  <div className="mt-4 space-y-2 text-xs">
                    <Meter label="CPU" value={m.cpu ?? 0} text={`${Math.round(m.cpu ?? 0)}%`} />
                    <Meter label="Memory" value={m.mem ?? 0} text={`${Math.round(m.mem ?? 0)}% of ${bytes(m.mem_total)}`} />
                    {disk.total > 0 && <Meter label="Disk" value={pct(disk.total - disk.free, disk.total)} text={`${bytes(disk.free)} free`} />}
                    <div className="flex justify-between text-muted-foreground"><span>Network</span><span className="tabular">↑ {rate(m.up_bps)} · ↓ {rate(m.down_bps)}</span></div>
                  </div>
                )}
                <div className="mt-3 text-xs text-muted-foreground">Shared folders: {s.roots?.length ? s.roots.map((r) => r.name).join(", ") : "none yet"}</div>
                <div className="mt-4 flex flex-wrap gap-2 border-t pt-3">
                  <Button size="sm" disabled={!on} onClick={() => { setServerId(s.id); router.push("/"); }}>Open</Button>
                  <Button size="sm" variant="outline" disabled={!on} onClick={() => { setServerId(s.id); router.push("/files"); }}>Files</Button>
                  <div className="flex-1" />
                  <Button size="icon" variant="ghost" aria-label="Rename" onClick={() => setRenaming(s)}><Pencil className="h-4 w-4" /></Button>
                  <Button size="icon" variant="ghost" aria-label="Remove" onClick={() => remove(s)}><Trash2 className="h-4 w-4" /></Button>
                </div>
              </Card>
            );
          })}
        </div>
      )}
      <AddServerDialog open={adding} onOpenChange={setAdding} />
      {renaming && <RenameDialog server={renaming} onClose={() => setRenaming(null)} />}
    </>
  );
}

function Meter({ label, value, text }: { label: string; value: number; text: string }) {
  return (
    <div>
      <div className="mb-0.5 flex justify-between"><span className="text-muted-foreground">{label}</span><span className="tabular">{text}</span></div>
      <Progress className="h-1.5" value={value} tone={value > 90 ? "danger" : value > 75 ? "warning" : "accent"} />
    </div>
  );
}

function AddServerDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [code, setCode] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true); setError(null);
    try {
      const { data, error: e } = await sb().rpc("create_enrollment", { p_name: name || null });
      if (e) throw e;
      setCode(makeLinkCode(String(data)));
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const close = (o: boolean) => {
    if (!o) { setCode(null); setName(""); setError(null); qc.invalidateQueries({ queryKey: ["servers"] }); }
    onOpenChange(o);
  };
  return (
    <Dialog open={open} onOpenChange={close} wide={!!code} title={code ? "Link your server" : "Add a server"}
      description={code ? "Run one of these on the computer you're adding. The code works once and expires in 30 minutes." : "Home PC (Windows) or rented Linux VPS — the same Personal Cloud agent runs on both."}
      footer={code ? <Button onClick={() => close(false)}>Done</Button> : <><Button variant="outline" onClick={() => close(false)}>Cancel</Button><Button onClick={create} loading={busy}>Create link code</Button></>}>
      {!code ? (
        <div className="space-y-3">
          <Field label="Name (optional)" hint="e.g. Home PC, Hetzner VPS"><Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} /></Field>
          <ErrorBox error={error} />
        </div>
      ) : (
        <div className="space-y-4 text-sm">
          <div>
            <div className="mb-1 font-medium">Windows PC with Personal Cloud already installed</div>
            <p className="mb-1 text-muted-foreground">PowerShell in the Personal Cloud folder:</p>
            <CopyBox value={`scripts\\pcloud.ps1 link ${code}${name ? ` --name "${name}"` : ""}`} />
          </div>
          <div>
            <div className="mb-1 font-medium">Windows PC, fresh install</div>
            <CopyBox multiline value={`git clone ${REPO} C:\\PersonalCloudOS\ncd C:\\PersonalCloudOS\npowershell -ExecutionPolicy Bypass -File scripts\\install.ps1\nscripts\\pcloud.ps1 link ${code}`} />
          </div>
          <div>
            <div className="mb-1 font-medium">Linux VPS (Ubuntu/Debian)</div>
            <CopyBox multiline value={`sudo git clone ${REPO} /opt/personal-cloud\nsudo bash /opt/personal-cloud/scripts/install-linux.sh --link ${code}`} />
          </div>
          <Notice>The server appears here as <b>online</b> within a minute. It only connects outwards — no ports are opened. Shared folders are still chosen on the server itself.</Notice>
        </div>
      )}
    </Dialog>
  );
}

function RenameDialog({ server, onClose }: { server: Server; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(server.name);
  const [error, setError] = useState<unknown>(null);
  const save = async () => {
    const { error: e } = await sb().from("servers").update({ name: name.trim() }).eq("id", server.id);
    if (e) setError(e); else { qc.invalidateQueries({ queryKey: ["servers"] }); onClose(); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Rename server"
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button onClick={save} disabled={!name.trim()}>Save</Button></>}>
      <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} autoFocus />
      <ErrorBox error={error} className="mt-2" />
    </Dialog>
  );
}
