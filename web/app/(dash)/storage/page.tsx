"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, HardDrive, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api, del, patch, post, type Root } from "@/lib/api";
import { bytes, date, pct } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { Bars } from "@/components/charts";
import { AddRootDialog } from "@/components/root-form";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorBox, Loading, PageHeader, Progress, Switch, Table, Td, Th, useConfirm, useToast } from "@/components/ui";

type Stats = {
  files: number; folders: number; total: number; used: number; free: number;
  drives: { drive: string; total: number; used: number; free: number; roots: string[] }[];
  by_type: { ext: string; count: number; size: number }[];
  largest: { root_id: number; path: string; name: string; size: number; modified: number }[];
  recent: { root_id: number; path: string; name: string; size: number; modified: number }[];
  by_root: { root_id: number; size: number; files: number }[];
  top_folders: { root_id: number; folder: string; size: number; files: number }[];
  health: { name: string; media: string; health: string; status: string; size: number }[];
  transfer_activity_7d: { action: string; count: number }[];
};

export default function StoragePage() {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, confirmNode] = useConfirm();
  const [adding, setAdding] = useState(false);
  const isAdmin = me.data?.user.role === "admin";
  const roots = useQuery({ queryKey: ["roots"], queryFn: () => api<Root[]>("/api/roots") });
  const stats = useQuery({ queryKey: ["storage-stats"], queryFn: () => api<Stats>("/api/storage/stats"), refetchInterval: 30000 });
  const name = (id: number) => roots.data?.find((r) => r.id === id)?.name || "?";

  const update = async (r: Root, changes: Partial<Root>) => {
    try { await patch(`/api/roots/${r.id}`, changes); qc.invalidateQueries({ queryKey: ["roots"] }); qc.invalidateQueries({ queryKey: ["me"] }); }
    catch (e) { toast("error", (e as Error).message); }
  };
  const remove = async (r: Root) => {
    if (!(await confirm(`Stop sharing “${r.name}”?`, { body: <>The folder <code>{r.path}</code> and its files stay on your PC untouched. It just won’t be accessible through Personal Cloud OS anymore.</>, confirm: "Stop sharing", danger: true }))) return;
    await del(`/api/roots/${r.id}`);
    qc.invalidateQueries({ queryKey: ["roots"] }); qc.invalidateQueries({ queryKey: ["me"] });
  };
  const reindex = async () => {
    const r = await post<{ id: number }>("/api/jobs", { type: "index_scan", params: {} });
    toast("success", `Re-indexing started (task #${r.id})`);
  };

  return (
    <>
      {confirmNode}
      <PageHeader title="Drives & Storage" description="Approved folders, disk space and what is using it."
        actions={isAdmin && <><Button variant="outline" onClick={reindex}><RefreshCw className="h-4 w-4" />Re-index</Button><Button onClick={() => setAdding(true)}><FolderPlus className="h-4 w-4" />Add folder</Button></>} />

      {stats.data && (
        <div className="mb-4 grid gap-4 md:grid-cols-3">
          <Card className="p-4 md:col-span-2">
            <div className="text-xs font-medium text-muted-foreground">Server storage (drives holding approved folders)</div>
            <div className="mt-2 text-2xl font-semibold tabular">{bytes(stats.data.used)} <span className="text-base font-normal text-muted-foreground">used of {bytes(stats.data.total)}</span></div>
            <Progress className="mt-3" value={pct(stats.data.used, stats.data.total)} />
            <div className="mt-2 text-sm text-muted-foreground">{bytes(stats.data.free)} available · {stats.data.files.toLocaleString()} files in {stats.data.folders.toLocaleString()} folders indexed</div>
          </Card>
          <Card className="p-4">
            <div className="text-xs font-medium text-muted-foreground">Transfers (last 7 days)</div>
            <ul className="mt-2 space-y-1 text-sm">
              {stats.data.transfer_activity_7d.length ? stats.data.transfer_activity_7d.map((a) => (
                <li key={a.action} className="flex justify-between"><span>{a.action.replace("file.", "").replace("dav.", "WebDAV ").replace("_", " ")}</span><span className="tabular">{a.count}</span></li>
              )) : <li className="text-muted-foreground">No transfers yet</li>}
            </ul>
          </Card>
        </div>
      )}

      <Card className="mb-4">
        <CardHeader title="Approved storage locations" description="Only these folders are reachable. Everything else on your PC stays private." />
        <CardBody>
          {roots.isLoading ? <Loading /> : roots.error ? <ErrorBox error={roots.error} /> : !roots.data?.length ? (
            <Empty icon={<HardDrive className="h-8 w-8" />} title="No storage folders approved yet" action={isAdmin && <Button onClick={() => setAdding(true)}>Add folder</Button>} />
          ) : (
            <div className="space-y-3">
              {roots.data.map((r) => {
                const used = stats.data?.by_root.find((b) => b.root_id === r.id);
                return (
                  <div key={r.id} className="rounded-lg border p-4">
                    <div className="flex flex-wrap items-start gap-3">
                      <HardDrive className="mt-0.5 h-5 w-5 text-muted-foreground" />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{r.name}</span>
                          {!r.available && <Badge tone="danger">unavailable — drive disconnected?</Badge>}
                          {r.read_only && <Badge>read-only</Badge>}{r.access === "r" && !r.read_only && <Badge>read-only for you</Badge>}
                        </div>
                        <div className="truncate font-mono text-xs text-muted-foreground">{r.path}</div>
                        <div className="mt-1 text-xs text-muted-foreground">{used ? `${bytes(used.size)} in ${used.files.toLocaleString()} files` : "Indexing…"}{r.disk && ` · drive: ${bytes(r.disk.free)} free of ${bytes(r.disk.total)}`}</div>
                      </div>
                      {isAdmin && <Button size="sm" variant="ghost" onClick={() => remove(r)} aria-label="Stop sharing"><Trash2 className="h-4 w-4" /></Button>}
                    </div>
                    {isAdmin && (
                      <div className="mt-3 grid gap-x-6 border-t pt-2 sm:grid-cols-2 lg:grid-cols-3">
                        <Switch checked={r.read_only} onCheckedChange={(v) => update(r, { read_only: v })} label="Read-only" />
                        <Switch checked={r.webdav} onCheckedChange={(v) => update(r, { webdav: v })} label="Mobile / WebDAV" />
                        <Switch checked={r.versioning} onCheckedChange={(v) => update(r, { versioning: v })} label="Version history" />
                        <Switch checked={r.index_content} onCheckedChange={(v) => update(r, { index_content: v })} label="Index contents" />
                        <Switch checked={r.ai_read} onCheckedChange={(v) => update(r, { ai_read: v })} label="AI can read" />
                        <Switch checked={r.ai_write} disabled={!r.ai_read} onCheckedChange={(v) => update(r, { ai_write: v })} label="AI can write" />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </CardBody>
      </Card>

      {stats.data && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card><CardHeader title="Usage by folder" /><CardBody>
            {stats.data.top_folders.length ? <Bars items={stats.data.top_folders.map((f) => ({ label: `${name(f.root_id)} / ${f.folder}`, value: f.size }))} format={bytes} /> : <p className="text-sm text-muted-foreground">No data yet</p>}
          </CardBody></Card>
          <Card><CardHeader title="Usage by file type" /><CardBody>
            {stats.data.by_type.length ? <Bars items={stats.data.by_type.map((t) => ({ label: `.${t.ext} (${t.count.toLocaleString()})`, value: t.size }))} format={bytes} /> : <p className="text-sm text-muted-foreground">No data yet</p>}
          </CardBody></Card>
          <Card><CardHeader title="Largest files" /><Table><thead><tr><Th>File</Th><Th className="text-right">Size</Th></tr></thead><tbody>
            {stats.data.largest.map((f) => (<tr key={`${f.root_id}${f.path}`}><Td><Link className="hover:text-accent" href={`/files?root=${f.root_id}&path=${encodeURIComponent(f.path.split("/").slice(0, -1).join("/"))}`}>{f.name}</Link><div className="truncate text-xs text-muted-foreground">{name(f.root_id)}/{f.path}</div></Td><Td className="text-right tabular">{bytes(f.size)}</Td></tr>))}
          </tbody></Table></Card>
          <Card><CardHeader title="Recently modified" /><Table><thead><tr><Th>File</Th><Th className="text-right">Modified</Th></tr></thead><tbody>
            {stats.data.recent.map((f) => (<tr key={`${f.root_id}${f.path}`}><Td><div className="truncate">{f.name}</div><div className="truncate text-xs text-muted-foreground">{name(f.root_id)}/{f.path}</div></Td><Td className="whitespace-nowrap text-right">{date(f.modified)}</Td></tr>))}
          </tbody></Table></Card>
          {isAdmin && (
            <Card className="lg:col-span-2"><CardHeader title="Drive health" description="Reported by Windows (Get-PhysicalDisk)." />
              <Table><thead><tr><Th>Disk</Th><Th>Type</Th><Th>Health</Th><Th>Status</Th><Th className="text-right">Size</Th></tr></thead><tbody>
                {stats.data.health.length ? stats.data.health.map((h) => (
                  <tr key={h.name}><Td>{h.name}</Td><Td>{h.media}</Td><Td><Badge tone={h.health === "Healthy" ? "success" : "danger"}>{h.health}</Badge></Td><Td>{h.status}</Td><Td className="text-right">{bytes(h.size)}</Td></tr>
                )) : <tr><Td colSpan={5} className="text-muted-foreground">Drive health is not available on this system.</Td></tr>}
                {stats.data.drives.map((d) => (
                  <tr key={d.drive}><Td>Volume {d.drive}</Td><Td colSpan={2}>{d.roots.join(", ")}</Td><Td>{pct(d.used, d.total)}% used</Td><Td className="text-right">{bytes(d.free)} free</Td></tr>
                ))}
              </tbody></Table>
            </Card>
          )}
        </div>
      )}
      <AddRootDialog open={adding} onOpenChange={setAdding} />
    </>
  );
}
