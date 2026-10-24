"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { History, Play, Plus, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, del, patch, post, type Root } from "@/lib/api";
import { ago, bytes, date } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { HostFolderPicker } from "@/components/folder-picker";
import {
  Badge, Button, Card, CardBody, CardHeader, CopyBox, Dialog, Empty, ErrorBox, Field, Input, Loading, Notice, PageHeader, Select, Switch,
  Table, Td, Th, useConfirm, useToast,
} from "@/components/ui";

type Pair = { id: number; name: string; root_id: number; rel_path: string; dest_path: string; mode: string; interval_minutes: number; enabled: number; last_run: number | null; last_status: string | null; last_error: string | null; open_conflicts: number };
type Plan = { id: number; name: string; root_ids: number[]; destination: string; encrypted: number; interval_hours: number; keep_snapshots: number; enabled: number; last_run: number | null; last_status: string | null; last_error: string | null; has_key: boolean | null; destination_available: boolean };
type Snapshot = { id: string; created: string; stats?: { files: number; bytes: number; new_bytes: number }; roots: { root_id: number; name: string }[]; error?: string };

const statusTone = (s: string | null) => s === "ok" ? "success" : s === "error" ? "danger" : s ? "warning" : "neutral";

export default function SyncPage() {
  const me = useMe();
  const roots = me.data?.roots || [];
  return (
    <>
      <PageHeader title="Sync & Backup" description="Sync keeps folders identical. Backups keep dated snapshots you can roll back to." />
      <Notice tone="warning" className="mb-4"><b>Sync is not a backup.</b> If a file is deleted or encrypted by ransomware, sync copies that damage. Keep at least one backup plan with several snapshots, ideally on a different drive.</Notice>
      <BackupSection roots={roots} />
      <SyncSection roots={roots} />
    </>
  );
}

function BackupSection({ roots }: { roots: Root[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, node] = useConfirm();
  const plans = useQuery({ queryKey: ["backups"], queryFn: () => api<Plan[]>("/api/backups"), refetchInterval: 10000 });
  const [adding, setAdding] = useState(false);
  const [restorePlan, setRestorePlan] = useState<Plan | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["backups"] });
  const run = async (p: Plan, what: "run" | "verify") => {
    try { const r = await post<{ job_id: number }>(`/api/backups/${p.id}/${what}`); toast("success", `${what === "run" ? "Backup" : "Verification"} started (task #${r.job_id})`); }
    catch (e) { toast("error", (e as Error).message); }
  };
  const remove = async (p: Plan) => {
    if (await confirm(`Delete backup plan “${p.name}”?`, { body: "Existing backup data at the destination is kept; only the schedule is removed.", danger: true, confirm: "Delete plan" })) { await del(`/api/backups/${p.id}`); refresh(); }
  };
  return (
    <Card className="mb-4">
      {node}
      <CardHeader title={<span className="flex items-center gap-2"><ShieldCheck className="h-4 w-4" />Backups</span>} description="Deduplicated, versioned, optionally AES-256 encrypted snapshots."
        action={<Button size="sm" onClick={() => setAdding(true)}><Plus className="h-4 w-4" />New backup plan</Button>} />
      {plans.isLoading ? <Loading /> : !plans.data?.length ? <CardBody><Empty title="No backup plans" description="Create one that copies your storage folders to a second drive every day." /></CardBody> : (
        <Table><thead><tr><Th>Plan</Th><Th>Destination</Th><Th>Schedule</Th><Th>Last run</Th><Th /></tr></thead><tbody>
          {plans.data.map((p) => (
            <tr key={p.id}>
              <Td><div className="font-medium">{p.name}</div><div className="text-xs text-muted-foreground">{p.root_ids.map((id) => roots.find((r) => r.id === id)?.name || `#${id}`).join(", ")}</div>
                {p.encrypted ? <Badge tone="accent" className="mt-1">encrypted</Badge> : <Badge className="mt-1">not encrypted</Badge>}</Td>
              <Td className="font-mono text-xs">{p.destination}{!p.destination_available && <div><Badge tone="danger">drive not connected</Badge></div>}</Td>
              <Td><Switch checked={!!p.enabled} onCheckedChange={async (v) => { await patch(`/api/backups/${p.id}`, { enabled: v }); refresh(); }} /><div className="text-xs text-muted-foreground">every {p.interval_hours}h · keep {p.keep_snapshots}</div></Td>
              <Td><Badge tone={statusTone(p.last_status)}>{p.last_status || "never"}</Badge><div className="text-xs text-muted-foreground">{ago(p.last_run)}</div>{p.last_error && <div className="max-w-xs truncate text-xs text-danger" title={p.last_error}>{p.last_error}</div>}</Td>
              <Td className="whitespace-nowrap text-right">
                <Button size="sm" variant="outline" onClick={() => run(p, "run")}><Play className="h-4 w-4" />Run now</Button>
                <Button size="sm" variant="ghost" onClick={() => run(p, "verify")}>Verify</Button>
                <Button size="sm" variant="ghost" onClick={() => setRestorePlan(p)}><History className="h-4 w-4" />Restore</Button>
                <Button size="sm" variant="ghost" onClick={() => remove(p)} aria-label="Delete plan"><Trash2 className="h-4 w-4" /></Button>
              </Td>
            </tr>
          ))}
        </tbody></Table>
      )}
      <NewBackup open={adding} onOpenChange={setAdding} roots={roots} onDone={refresh} />
      {restorePlan && <RestoreDialog plan={restorePlan} roots={roots} onClose={() => setRestorePlan(null)} />}
    </Card>
  );
}

function NewBackup({ open, onOpenChange, roots, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; roots: Root[]; onDone: () => void }) {
  const [name, setName] = useState("Daily backup");
  const [dest, setDest] = useState("");
  const [ids, setIds] = useState<number[]>([]);
  const [encrypted, setEncrypted] = useState(true);
  const [hours, setHours] = useState(24);
  const [keep, setKeep] = useState(30);
  const [picker, setPicker] = useState(false);
  const [key, setKey] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const selected = ids.length ? ids : roots.map((r) => r.id);
  const create = async () => {
    setError(null);
    try {
      const r = await post<{ recovery_key: string | null }>("/api/backups", { name, root_ids: selected, destination: dest, encrypted, interval_hours: hours, keep_snapshots: keep });
      onDone();
      if (r.recovery_key) setKey(r.recovery_key); else onOpenChange(false);
    } catch (e) { setError(e); }
  };
  const close = (o: boolean) => { if (!o) setKey(null); onOpenChange(o); };
  return (
    <>
      <Dialog open={open} onOpenChange={close} title={key ? "Save your recovery key" : "New backup plan"}
        footer={key ? <Button onClick={() => close(false)}>I saved it</Button> : <><Button variant="outline" onClick={() => close(false)}>Cancel</Button><Button onClick={create} disabled={!dest}>Create</Button></>}>
        {key ? (
          <div className="space-y-3"><Notice tone="warning">This key decrypts your backups. It is stored protected on this PC, but if this PC dies you need it to restore elsewhere. It will not be shown again.</Notice><CopyBox value={key} /></div>
        ) : (
          <div className="space-y-3">
            <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Destination folder" hint="Use another physical drive or a NAS share."><div className="flex gap-2"><Input placeholder="E:\Backups" value={dest} onChange={(e) => setDest(e.target.value)} /><Button variant="outline" onClick={() => setPicker(true)}>Browse</Button></div></Field>
            <Field label="Locations to back up">
              <div className="space-y-1">{roots.map((r) => (
                <label key={r.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selected.includes(r.id)} onChange={(e) => setIds(e.target.checked ? [...new Set([...selected, r.id])] : selected.filter((x) => x !== r.id))} />{r.name} <span className="font-mono text-xs text-muted-foreground">{r.path}</span></label>
              ))}</div>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Run every (hours)"><Input type="number" min={1} value={hours} onChange={(e) => setHours(Number(e.target.value))} /></Field>
              <Field label="Snapshots to keep"><Input type="number" min={1} value={keep} onChange={(e) => setKeep(Number(e.target.value))} /></Field>
            </div>
            <Switch checked={encrypted} onCheckedChange={setEncrypted} label="Encrypt (AES-256-GCM)" description="Recommended, especially for removable or network destinations." />
            <ErrorBox error={error} />
          </div>
        )}
      </Dialog>
      <HostFolderPicker open={picker} onOpenChange={setPicker} onPick={setDest} title="Choose backup destination" />
    </>
  );
}

function RestoreDialog({ plan, roots, onClose }: { plan: Plan; roots: Root[]; onClose: () => void }) {
  const toast = useToast();
  const snaps = useQuery({ queryKey: ["snapshots", plan.id], queryFn: () => api<Snapshot[]>(`/api/backups/${plan.id}/snapshots`) });
  const [snap, setSnap] = useState("");
  const [target, setTarget] = useState(roots.find((r) => r.access === "rw")?.id);
  const [prefix, setPrefix] = useState("");
  const [key, setKey] = useState("");
  const [error, setError] = useState<unknown>(null);
  const restore = async () => {
    setError(null);
    try {
      const r = await post<{ job_id: number }>(`/api/backups/${plan.id}/restore`, { snapshot: snap, target_root_id: target, prefix, recovery_key: key || null });
      toast("success", `Restore started (task #${r.job_id}). Files go into a new “Restored …” folder — nothing is overwritten.`);
      onClose();
    } catch (e) { setError(e); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} wide title={`Restore from “${plan.name}”`} description="Restored files are written to a new folder so your current files are never overwritten."
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button onClick={restore} disabled={!snap || !target}>Restore</Button></>}>
      <div className="space-y-3">
        {snaps.isLoading ? <Loading /> : snaps.error ? <ErrorBox error={snaps.error} /> : !snaps.data?.length ? <Empty title="No snapshots yet" description="Run the backup first." /> : (
          <div className="max-h-64 overflow-y-auto rounded-md border">
            {snaps.data.map((s) => (
              <label key={s.id} className="flex cursor-pointer items-center gap-3 border-b px-3 py-2 text-sm last:border-0 hover:bg-muted">
                <input type="radio" name="snap" checked={snap === s.id} onChange={() => setSnap(s.id)} />
                <span className="flex-1">{s.error ? s.id : date(new Date(s.created).getTime() / 1000)}</span>
                {s.stats && <span className="text-xs text-muted-foreground">{s.stats.files.toLocaleString()} files · {bytes(s.stats.bytes)} · +{bytes(s.stats.new_bytes)} new</span>}
                {s.error && <Badge tone="danger">{s.error}</Badge>}
              </label>
            ))}
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Restore into location"><Select className="w-full" value={target} onChange={(e) => setTarget(Number(e.target.value))}>{roots.filter((r) => r.access === "rw").map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</Select></Field>
          <Field label="Only this path (optional)" hint="e.g. Documents/Taxes — leave empty for everything"><Input value={prefix} onChange={(e) => setPrefix(e.target.value)} /></Field>
        </div>
        {plan.encrypted ? <Field label="Recovery key (only if this PC lost its key)" hint={plan.has_key ? "Not needed — the key is stored on this PC." : "Required: the key is not on this PC."}><Input value={key} onChange={(e) => setKey(e.target.value)} /></Field> : null}
        <ErrorBox error={error} />
      </div>
    </Dialog>
  );
}

function SyncSection({ roots }: { roots: Root[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, node] = useConfirm();
  const pairs = useQuery({ queryKey: ["sync"], queryFn: () => api<Pair[]>("/api/sync"), refetchInterval: 10000 });
  const [adding, setAdding] = useState(false);
  const [conflicts, setConflicts] = useState<Pair | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["sync"] });
  const remove = async (p: Pair) => { if (await confirm(`Remove sync “${p.name}”?`, { body: "Files on both sides stay as they are.", danger: true, confirm: "Remove" })) { await del(`/api/sync/${p.id}`); refresh(); } };
  return (
    <Card>
      {node}
      <CardHeader title={<span className="flex items-center gap-2"><RefreshCw className="h-4 w-4" />Folder sync</span>}
        description="Keep a storage folder in sync with another folder (second drive, NAS, laptop share). Phones sync via WebDAV apps such as FolderSync."
        action={<Button size="sm" variant="outline" onClick={() => setAdding(true)}><Plus className="h-4 w-4" />New sync</Button>} />
      {pairs.isLoading ? <Loading /> : !pairs.data?.length ? <CardBody><Empty title="No sync rules" /></CardBody> : (
        <Table><thead><tr><Th>Rule</Th><Th>Mode</Th><Th>Schedule</Th><Th>Status</Th><Th /></tr></thead><tbody>
          {pairs.data.map((p) => (
            <tr key={p.id}>
              <Td><div className="font-medium">{p.name}</div><div className="font-mono text-xs text-muted-foreground">{roots.find((r) => r.id === p.root_id)?.name}/{p.rel_path} ⇄ {p.dest_path}</div></Td>
              <Td><Badge>{p.mode === "mirror" ? "mirror →" : "two-way ⇄"}</Badge></Td>
              <Td><Switch checked={!!p.enabled} onCheckedChange={async (v) => { await patch(`/api/sync/${p.id}`, { enabled: v }); refresh(); }} /><div className="text-xs text-muted-foreground">every {p.interval_minutes} min</div></Td>
              <Td><Badge tone={statusTone(p.last_status)}>{p.last_status || "never"}</Badge> {p.open_conflicts > 0 && <button onClick={() => setConflicts(p)}><Badge tone="warning">{p.open_conflicts} conflicts</Badge></button>}
                <div className="text-xs text-muted-foreground">{ago(p.last_run)}</div>{p.last_error && <div className="max-w-xs truncate text-xs text-danger">{p.last_error}</div>}</Td>
              <Td className="whitespace-nowrap text-right">
                <Button size="sm" variant="outline" onClick={async () => { const r = await post<{ job_id: number }>(`/api/sync/${p.id}/run`); toast("success", `Sync started (task #${r.job_id})`); }}><Play className="h-4 w-4" />Sync now</Button>
                <Button size="sm" variant="ghost" onClick={() => remove(p)} aria-label="Remove"><Trash2 className="h-4 w-4" /></Button>
              </Td>
            </tr>
          ))}
        </tbody></Table>
      )}
      <NewSync open={adding} onOpenChange={setAdding} roots={roots} onDone={refresh} />
      {conflicts && <ConflictsDialog pair={conflicts} onClose={() => { setConflicts(null); refresh(); }} />}
    </Card>
  );
}

function NewSync({ open, onOpenChange, roots, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; roots: Root[]; onDone: () => void }) {
  const [form, setForm] = useState({ name: "", root_id: roots[0]?.id, rel_path: "", dest_path: "", mode: "mirror", interval_minutes: 60 });
  const [picker, setPicker] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const create = async () => {
    setError(null);
    try { await post("/api/sync", { ...form, root_id: form.root_id ?? roots[0]?.id, name: form.name || "Sync" }); onDone(); onOpenChange(false); } catch (e) { setError(e); }
  };
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange} title="New sync rule"
        footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button onClick={create} disabled={!form.dest_path}>Create</Button></>}>
        <div className="space-y-3">
          <Field label="Name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Documents → E drive" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Source location"><Select className="w-full" value={form.root_id} onChange={(e) => setForm({ ...form, root_id: Number(e.target.value) })}>{roots.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</Select></Field>
            <Field label="Sub-folder (optional)"><Input value={form.rel_path} onChange={(e) => setForm({ ...form, rel_path: e.target.value })} /></Field>
          </div>
          <Field label="Destination folder"><div className="flex gap-2"><Input placeholder="E:\Mirror\Documents" value={form.dest_path} onChange={(e) => setForm({ ...form, dest_path: e.target.value })} /><Button variant="outline" onClick={() => setPicker(true)}>Browse</Button></div></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Mode"><Select className="w-full" value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}><option value="mirror">Mirror (source → destination)</option><option value="two_way">Two-way</option></Select></Field>
            <Field label="Every (minutes)"><Input type="number" min={5} value={form.interval_minutes} onChange={(e) => setForm({ ...form, interval_minutes: Number(e.target.value) })} /></Field>
          </div>
          <p className="text-xs text-muted-foreground">Files that sync would overwrite or delete are moved to a sync archive in the data folder, and two-way conflicts keep both copies.</p>
          <ErrorBox error={error} />
        </div>
      </Dialog>
      <HostFolderPicker open={picker} onOpenChange={setPicker} onPick={(p) => setForm((f) => ({ ...f, dest_path: p }))} title="Choose sync destination" />
    </>
  );
}

function ConflictsDialog({ pair, onClose }: { pair: Pair; onClose: () => void }) {
  const q = useQuery({ queryKey: ["conflicts", pair.id], queryFn: () => api<{ id: number; rel_path: string; detail: string; created_at: number; resolved: number }[]>(`/api/sync/${pair.id}/conflicts`) });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Conflicts — ${pair.name}`} description="Both copies were kept. Review the files, delete the one you don’t need, then mark as resolved.">
      <ul className="divide-y rounded-md border">
        {q.data?.filter((c) => !c.resolved).map((c) => (
          <li key={c.id} className="flex items-center gap-2 p-3 text-sm"><div className="flex-1"><div className="font-mono text-xs">{c.rel_path}</div><div className="text-xs text-muted-foreground">{c.detail} · {date(c.created_at)}</div></div>
            <Button size="sm" variant="outline" onClick={async () => { await post(`/api/sync/conflicts/${c.id}/resolve`); q.refetch(); }}>Resolved</Button></li>
        ))}
      </ul>
    </Dialog>
  );
}
