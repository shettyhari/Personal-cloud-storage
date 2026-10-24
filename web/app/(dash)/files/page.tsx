"use client";
import * as ContextMenu from "@radix-ui/react-context-menu";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownAZ, ChevronRight, Copy, Download, Eye, FileArchive, FolderInput, FolderPlus, FolderUp, Hash, History,
  LayoutGrid, List, MoreHorizontal, Pencil, RotateCcw, Trash2, Upload, X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, del, downloadUrl, post, qs, type FileEntry, type Root } from "@/lib/api";
import { bytes, cn, date } from "@/lib/format";
import { useHotkey, useMe } from "@/lib/hooks";
import { enqueueUploads, uploadStore } from "@/lib/uploads";
import { ChecksumDialog, MoveDialog, NewFolderDialog, PreviewDialog, RenameDialog, VersionsDialog } from "@/components/file-dialogs";
import { FileIcon, kindOf } from "@/components/file-icon";
import {
  Badge, Button, Card, Empty, ErrorBox, Loading, Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger, Select, useConfirm, useToast,
} from "@/components/ui";

type Listing = { root: Root; path: string; items: FileEntry[] };
type SortKey = "name" | "modified" | "size" | "kind";
type TrashItem = { id: number; root_id: number; rel_path: string; name: string; is_dir: number; size: number; deleted_at: number; deleted_by: string };

export default function FilesPage() {
  return <Suspense fallback={<Loading />}><Files /></Suspense>;
}

function Files() {
  const me = useMe();
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, confirmNode] = useConfirm();
  const roots = me.data?.roots || [];
  const rootId = Number(params.get("root")) || roots[0]?.id;
  const path = params.get("path") || "";
  const query = params.get("q") || "";
  const trashView = params.get("view") === "trash";
  const [layout, setLayout] = useState<"list" | "grid">("list");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "name", dir: 1 });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<FileEntry | null>(null);
  const [renaming, setRenaming] = useState<FileEntry | null>(null);
  const [versions, setVersions] = useState<FileEntry | null>(null);
  const [checksum, setChecksum] = useState<FileEntry | null>(null);
  const [moving, setMoving] = useState<{ items: FileEntry[]; copy: boolean } | null>(null);
  const [newFolder, setNewFolder] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try { const l = localStorage.getItem("pcloud-layout"); if (l === "grid" || l === "list") setLayout(l); } catch { /* ignore */ }
  }, []);
  const setLayoutSaved = (l: "list" | "grid") => { setLayout(l); try { localStorage.setItem("pcloud-layout", l); } catch { /* ignore */ } };

  const go = useCallback((o: { root?: number; path?: string; q?: string; view?: string }) => {
    setSelected(new Set());
    router.push(`/files?${qs({ root: o.root ?? rootId, path: o.path, q: o.q, view: o.view })}`);
  }, [router, rootId]);

  const listing = useQuery({
    queryKey: ["files", rootId, path],
    queryFn: () => api<Listing>(`/api/files/list?${qs({ root_id: rootId, path })}`),
    enabled: !!rootId && !query && !trashView,
  });
  const search = useQuery({
    queryKey: ["search", query],
    queryFn: async () => (await api<FileEntry[]>(`/api/search?${qs({ q: query, limit: 200 })}`)).map((f) => ({ ...f, kind: f.kind || kindOf(f.name, f.is_dir) })),
    enabled: !!query,
  });
  const trash = useQuery({ queryKey: ["trash"], queryFn: () => api<TrashItem[]>("/api/trash"), enabled: trashView });

  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["files"] });
    qc.invalidateQueries({ queryKey: ["search"] });
    qc.invalidateQueries({ queryKey: ["trash"] });
  }, [qc]);
  useEffect(() => { const off = uploadStore.onComplete(() => refresh()); return () => { off(); }; }, [refresh]);

  const root = roots.find((r) => r.id === rootId);
  const canWrite = root?.access === "rw";
  const items = useMemo(() => {
    const src = query ? search.data || [] : listing.data?.items || [];
    return [...src].sort((a, b) => {
      if (!query && a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      const k = sort.key;
      const av = k === "name" ? a.name.toLowerCase() : k === "kind" ? a.kind : a[k];
      const bv = k === "name" ? b.name.toLowerCase() : k === "kind" ? b.kind : b[k];
      return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
    });
  }, [query, search.data, listing.data, sort]);

  const keyOf = (f: FileEntry) => `${f.root_id}:${f.path}`;
  const selItems = items.filter((f) => selected.has(keyOf(f)));
  const toggle = (f: FileEntry) => setSelected((s) => { const n = new Set(s); if (n.has(keyOf(f))) n.delete(keyOf(f)); else n.add(keyOf(f)); return n; });

  const open = (f: FileEntry) => {
    if (f.is_dir) go({ root: f.root_id, path: f.path });
    else setPreview(f);
  };
  const remove = async (list: FileEntry[]) => {
    if (!list.length) return;
    const ok = await confirm(`Move ${list.length === 1 ? `“${list[0].name}”` : `${list.length} items`} to the recycle bin?`,
      { body: "You can restore deleted items from the recycle bin until they expire.", danger: true, confirm: "Delete" });
    if (!ok) return;
    const r = await post<{ results: { ok: boolean; error?: string }[] }>("/api/files/delete", { items: list.map((i) => ({ root_id: i.root_id, path: i.path })) });
    const failed = r.results.filter((x) => !x.ok);
    toast(failed.length ? "error" : "success", failed.length ? `Could not delete: ${failed[0].error}` : `Moved ${list.length} item(s) to the recycle bin`);
    setSelected(new Set()); refresh();
  };
  const downloadMany = async (list: FileEntry[]) => {
    if (list.length === 1 && !list[0].is_dir) { location.href = downloadUrl(list[0].root_id, list[0].path); return; }
    toast("info", "Preparing ZIP…");
    try {
      const r = await post<{ ticket: string }>("/api/files/zip", { items: list.map((i) => ({ root_id: i.root_id, path: i.path })), name: list.length === 1 ? list[0].name : "download" });
      location.href = `/api/files/zip/${r.ticket}`;
    } catch (e) { toast("error", (e as Error).message); }
  };
  const runJob = async (type: string, params: Record<string, unknown>, label: string) => {
    try { const r = await post<{ id: number }>("/api/jobs", { type, params }); toast("success", `${label} started (task #${r.id}). See Background Tasks.`); }
    catch (e) { toast("error", (e as Error).message); }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragging(false);
    if (!canWrite || !rootId) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length) enqueueUploads(files, rootId, path);
  };

  useHotkey("Delete", useCallback(() => { if (selItems.length && canWrite) remove(selItems); }, [selItems, canWrite])); // eslint-disable-line react-hooks/exhaustive-deps
  useHotkey("Escape", useCallback(() => setSelected(new Set()), []));

  if (me.isLoading) return <Loading />;
  if (!roots.length) {
    return <Card><Empty icon={<FolderPlus className="h-8 w-8" />} title="No storage locations yet"
      description="Approve a folder on your PC (for example D:\PersonalCloud) to start using your cloud."
      action={me.data?.user.role === "admin" ? <Link href="/storage"><Button>Add storage folder</Button></Link> : undefined} /></Card>;
  }

  const crumbs = path ? path.split("/") : [];
  const loading = query ? search.isLoading : listing.isLoading;
  const error = query ? search.error : listing.error;

  const actionsFor = (f: FileEntry, Item: typeof MenuItem | typeof CtxItem) => {
    const fRoot = roots.find((r) => r.id === f.root_id);
    const w = fRoot?.access === "rw";
    const archive = ["zip", "tar", "gz", "tgz"].includes(f.ext);
    const parent = f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "";
    return (
      <>
        <Item icon={<Eye className="h-4 w-4" />} onSelect={() => open(f)}>{f.is_dir ? "Open" : "Preview"}</Item>
        {query && <Item icon={<FolderInput className="h-4 w-4" />} onSelect={() => go({ root: f.root_id, path: parent })}>Show in folder</Item>}
        <Item icon={<Download className="h-4 w-4" />} onSelect={() => downloadMany([f])}>{f.is_dir ? "Download as ZIP" : "Download"}</Item>
        {w && <Item icon={<Pencil className="h-4 w-4" />} onSelect={() => setRenaming(f)}>Rename</Item>}
        {w && <Item icon={<FolderInput className="h-4 w-4" />} onSelect={() => setMoving({ items: [f], copy: false })}>Move to…</Item>}
        <Item icon={<Copy className="h-4 w-4" />} onSelect={() => setMoving({ items: [f], copy: true })}>Copy to…</Item>
        {!f.is_dir && <Item icon={<History className="h-4 w-4" />} onSelect={() => setVersions(f)}>Version history</Item>}
        {!f.is_dir && <Item icon={<Hash className="h-4 w-4" />} onSelect={() => setChecksum(f)}>Checksum</Item>}
        {w && <Item icon={<FileArchive className="h-4 w-4" />} onSelect={() => runJob("compress", { root_id: f.root_id, paths: [f.path], dest_dir: parent, archive_name: `${f.name}.zip` }, "Compression")}>Compress to ZIP</Item>}
        {w && archive && <Item icon={<FolderUp className="h-4 w-4" />} onSelect={() => runJob("extract", { root_id: f.root_id, path: f.path, dest_dir: parent }, "Extraction")}>Extract here</Item>}
        {w && <><Sep /><Item danger icon={<Trash2 className="h-4 w-4" />} onSelect={() => remove([f])}>Delete</Item></>}
      </>
    );
  };

  return (
    <div onDragOver={(e) => { if (canWrite && !query && !trashView) { e.preventDefault(); setDragging(true); } }} onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }} onDrop={onDrop} className="relative">
      {confirmNode}
      {/* Location tabs */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {roots.map((r) => (
          <button key={r.id} onClick={() => go({ root: r.id, path: "" })}
            className={cn("rounded-full border px-3 py-1 text-sm", r.id === rootId && !query && !trashView ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
            {r.name}{r.access === "r" && <span className="ml-1 text-xs opacity-70">(read-only)</span>}
          </button>
        ))}
        <button onClick={() => go({ view: "trash" })} className={cn("ml-auto flex items-center gap-1 rounded-full border px-3 py-1 text-sm", trashView ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:bg-muted")}>
          <Trash2 className="h-3.5 w-3.5" />Recycle bin
        </button>
      </div>

      {trashView ? <TrashView trash={trash} refresh={refresh} confirm={confirm} roots={roots} /> : (
        <>
          {/* Toolbar */}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {query ? (
              <div className="flex items-center gap-2 text-sm"><span>Results for <b>“{query}”</b></span><Button size="sm" variant="ghost" onClick={() => go({ path: "" })}><X className="h-4 w-4" />Clear</Button></div>
            ) : (
              <nav className="flex min-w-0 flex-1 flex-wrap items-center gap-1 text-sm" aria-label="Breadcrumb">
                <button className="rounded px-1.5 py-0.5 font-medium hover:bg-muted" onClick={() => go({ path: "" })}>{root?.name}</button>
                {crumbs.map((c, i) => (
                  <span key={i} className="flex items-center gap-1"><ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                    <button className="rounded px-1.5 py-0.5 hover:bg-muted" onClick={() => go({ path: crumbs.slice(0, i + 1).join("/") })}>{c}</button></span>
                ))}
              </nav>
            )}
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {selItems.length > 0 && (
                <div className="flex items-center gap-1 rounded-md border bg-card px-1">
                  <span className="px-2 text-xs text-muted-foreground">{selItems.length} selected</span>
                  <Button size="sm" variant="ghost" onClick={() => downloadMany(selItems)}><Download className="h-4 w-4" /></Button>
                  {canWrite && <Button size="sm" variant="ghost" onClick={() => setMoving({ items: selItems, copy: false })}><FolderInput className="h-4 w-4" /></Button>}
                  <Button size="sm" variant="ghost" onClick={() => setMoving({ items: selItems, copy: true })}><Copy className="h-4 w-4" /></Button>
                  {canWrite && <Button size="sm" variant="ghost" onClick={() => remove(selItems)}><Trash2 className="h-4 w-4 text-danger" /></Button>}
                </div>
              )}
              <Select value={`${sort.key}:${sort.dir}`} onChange={(e) => { const [k, d] = e.target.value.split(":"); setSort({ key: k as SortKey, dir: Number(d) as 1 | -1 }); }} aria-label="Sort">
                <option value="name:1">Name A–Z</option><option value="name:-1">Name Z–A</option>
                <option value="modified:-1">Newest first</option><option value="modified:1">Oldest first</option>
                <option value="size:-1">Largest first</option><option value="size:1">Smallest first</option>
                <option value="kind:1">Type</option>
              </Select>
              <div className="flex rounded-md border">
                <button className={cn("p-2", layout === "list" && "bg-muted")} onClick={() => setLayoutSaved("list")} aria-label="List view"><List className="h-4 w-4" /></button>
                <button className={cn("p-2", layout === "grid" && "bg-muted")} onClick={() => setLayoutSaved("grid")} aria-label="Grid view"><LayoutGrid className="h-4 w-4" /></button>
              </div>
              {canWrite && !query && <>
                <Button variant="outline" onClick={() => setNewFolder(true)}><FolderPlus className="h-4 w-4" /><span className="hidden sm:inline">New folder</span></Button>
                <Menu>
                  <MenuTrigger asChild><Button><Upload className="h-4 w-4" />Upload</Button></MenuTrigger>
                  <MenuContent>
                    <MenuItem icon={<Upload className="h-4 w-4" />} onSelect={() => fileInput.current?.click()}>Files…</MenuItem>
                    <MenuItem icon={<FolderUp className="h-4 w-4" />} onSelect={() => folderInput.current?.click()}>Folder…</MenuItem>
                  </MenuContent>
                </Menu>
              </>}
            </div>
          </div>
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => { if (e.target.files && rootId) enqueueUploads(Array.from(e.target.files), rootId, path); e.target.value = ""; }} />
          <input ref={folderInput} type="file" multiple hidden {...{ webkitdirectory: "", directory: "" }} onChange={(e) => { if (e.target.files && rootId) enqueueUploads(Array.from(e.target.files), rootId, path); e.target.value = ""; }} />

          <Card className="overflow-hidden">
            {loading ? <Loading /> : error ? <ErrorBox className="m-4" error={error} /> : !items.length ? (
              <Empty icon={<FolderUp className="h-8 w-8" />} title={query ? "No matching files" : "This folder is empty"}
                description={query ? "Search covers file names and indexed document text. New files are indexed within a few minutes." : canWrite ? "Drag files here or use Upload." : undefined} />
            ) : layout === "list" ? (
              <table className="w-full text-sm">
                <thead className="bg-muted/40">
                  <tr>
                    <th className="w-10 px-3 py-2"><input type="checkbox" aria-label="Select all" checked={selItems.length === items.length && items.length > 0} onChange={(e) => setSelected(e.target.checked ? new Set(items.map(keyOf)) : new Set())} /></th>
                    {(["name", "modified", "size"] as SortKey[]).map((k) => (
                      <th key={k} className={cn("px-3 py-2 text-left text-xs font-medium text-muted-foreground", k !== "name" && "hidden sm:table-cell", k === "size" && "text-right")}>
                        <button className="inline-flex items-center gap-1 capitalize hover:text-foreground" onClick={() => setSort((s) => ({ key: k, dir: s.key === k ? (-s.dir as 1 | -1) : k === "name" ? 1 : -1 }))}>
                          {k === "modified" ? "Modified" : k}{sort.key === k && <ArrowDownAZ className={cn("h-3 w-3", sort.dir === -1 && "rotate-180")} />}
                        </button>
                      </th>
                    ))}
                    <th className="w-10" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((f) => (
                    <ContextMenu.Root key={keyOf(f)}>
                      <ContextMenu.Trigger asChild>
                        <tr className={cn("group border-t hover:bg-muted/50", selected.has(keyOf(f)) && "bg-accent-soft")} onDoubleClick={() => open(f)}>
                          <td className="px-3 py-2"><input type="checkbox" aria-label={`Select ${f.name}`} checked={selected.has(keyOf(f))} onChange={() => toggle(f)} /></td>
                          <td className="max-w-0 px-3 py-2">
                            <button className="flex w-full min-w-0 items-center gap-3 text-left" onClick={() => open(f)}>
                              <FileIcon kind={f.kind || kindOf(f.name, f.is_dir)} />
                              <span className="min-w-0">
                                <span className="block truncate">{f.name}</span>
                                {query && <span className="block truncate text-xs text-muted-foreground">{roots.find((r) => r.id === f.root_id)?.name}/{f.path}</span>}
                                {f.snippet && <span className="block truncate text-xs text-muted-foreground">…{f.snippet}…</span>}
                              </span>
                            </button>
                          </td>
                          <td className="hidden whitespace-nowrap px-3 py-2 text-muted-foreground sm:table-cell">{date(f.modified)}</td>
                          <td className="hidden whitespace-nowrap px-3 py-2 text-right text-muted-foreground tabular sm:table-cell">{f.is_dir ? "—" : bytes(f.size)}</td>
                          <td className="px-2 py-2">
                            <Menu>
                              <MenuTrigger asChild><button className="rounded p-1 text-muted-foreground opacity-60 hover:bg-muted group-hover:opacity-100" aria-label="Actions"><MoreHorizontal className="h-4 w-4" /></button></MenuTrigger>
                              <MenuContent>{actionsFor(f, MenuItem)}</MenuContent>
                            </Menu>
                          </td>
                        </tr>
                      </ContextMenu.Trigger>
                      <ContextMenu.Portal><ContextMenu.Content className="z-50 min-w-[190px] rounded-lg border bg-card p-1 shadow-lg">{actionsFor(f, CtxItem)}</ContextMenu.Content></ContextMenu.Portal>
                    </ContextMenu.Root>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="grid grid-cols-2 gap-3 p-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
                {items.map((f) => (
                  <ContextMenu.Root key={keyOf(f)}>
                    <ContextMenu.Trigger asChild>
                      <div className={cn("group relative flex flex-col overflow-hidden rounded-lg border bg-card hover:shadow-sm", selected.has(keyOf(f)) && "ring-2 ring-accent")}>
                        <input type="checkbox" className="absolute left-2 top-2 z-10 opacity-0 checked:opacity-100 group-hover:opacity-100" checked={selected.has(keyOf(f))} onChange={() => toggle(f)} aria-label={`Select ${f.name}`} />
                        <button onClick={() => open(f)} className="flex aspect-[4/3] items-center justify-center bg-muted/40">
                          {f.kind === "image" && f.size < 15 * 1024 * 1024
                            ? <img loading="lazy" src={downloadUrl(f.root_id, f.path, true)} alt="" className="h-full w-full object-cover" />
                            : <FileIcon kind={f.kind || kindOf(f.name, f.is_dir)} className="h-10 w-10" />}
                        </button>
                        <div className="flex items-center gap-1 p-2">
                          <div className="min-w-0 flex-1"><div className="truncate text-sm">{f.name}</div><div className="text-xs text-muted-foreground">{f.is_dir ? date(f.modified) : bytes(f.size)}</div></div>
                          <Menu><MenuTrigger asChild><button className="rounded p-1 text-muted-foreground hover:bg-muted" aria-label="Actions"><MoreHorizontal className="h-4 w-4" /></button></MenuTrigger><MenuContent>{actionsFor(f, MenuItem)}</MenuContent></Menu>
                        </div>
                      </div>
                    </ContextMenu.Trigger>
                    <ContextMenu.Portal><ContextMenu.Content className="z-50 min-w-[190px] rounded-lg border bg-card p-1 shadow-lg">{actionsFor(f, CtxItem)}</ContextMenu.Content></ContextMenu.Portal>
                  </ContextMenu.Root>
                ))}
              </div>
            )}
          </Card>
          {!query && listing.data && <p className="mt-2 text-xs text-muted-foreground">{items.length} items · {root?.path}{path ? `\\${path.replaceAll("/", "\\")}` : ""}</p>}
        </>
      )}

      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-accent bg-accent-soft/80">
          <div className="flex items-center gap-2 text-sm font-medium text-accent"><Upload className="h-5 w-5" />Drop to upload to {root?.name}/{path}</div>
        </div>
      )}
      <PreviewDialog file={preview} onClose={() => setPreview(null)} canWrite={roots.find((r) => r.id === preview?.root_id)?.access === "rw"} onSaved={refresh} />
      <RenameDialog file={renaming} onClose={() => setRenaming(null)} onDone={refresh} />
      <VersionsDialog file={versions} onClose={() => setVersions(null)} canWrite={roots.find((r) => r.id === versions?.root_id)?.access === "rw"} onDone={refresh} />
      <ChecksumDialog file={checksum} onClose={() => setChecksum(null)} />
      {rootId && <NewFolderDialog open={newFolder} onClose={() => setNewFolder(false)} rootId={rootId} path={path} onDone={refresh} />}
      {moving && <MoveDialog items={moving.items} copy={moving.copy} roots={roots} onClose={() => setMoving(null)} onDone={refresh} />}
    </div>
  );
}

function CtxItem({ children, onSelect, danger, icon }: { children: React.ReactNode; onSelect?: () => void; danger?: boolean; icon?: React.ReactNode }) {
  return (
    <ContextMenu.Item onSelect={onSelect} className={cn("flex cursor-pointer select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-muted", danger && "text-danger")}>
      {icon}{children}
    </ContextMenu.Item>
  );
}
function Sep() { return <div className="my-1 h-px bg-border" />; }
void MenuSeparator;

function TrashView({ trash, refresh, confirm, roots }: {
  trash: ReturnType<typeof useQuery<TrashItem[]>>; refresh: () => void;
  confirm: (t: string, o?: { body?: React.ReactNode; danger?: boolean; confirm?: string }) => Promise<boolean>; roots: Root[];
}) {
  const toast = useToast();
  const restore = async (t: TrashItem) => {
    try { await post(`/api/trash/${t.id}/restore`); toast("success", `Restored ${t.name}`); refresh(); } catch (e) { toast("error", (e as Error).message); }
  };
  const purge = async (t: TrashItem) => {
    if (!(await confirm(`Permanently delete “${t.name}”?`, { body: "This cannot be undone.", danger: true, confirm: "Delete forever" }))) return;
    await del(`/api/trash/${t.id}`); refresh();
  };
  const empty = async () => {
    if (!(await confirm("Empty the recycle bin?", { body: "All items will be permanently deleted. This cannot be undone.", danger: true, confirm: "Empty" }))) return;
    const r = await post<{ purged: number }>("/api/trash/empty"); toast("success", `Deleted ${r.purged} item(s)`); refresh();
  };
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="text-sm text-muted-foreground">Deleted items are kept here, then removed automatically after the retention period (Settings).</div>
        {!!trash.data?.length && <Button size="sm" variant="outline" onClick={empty}><Trash2 className="h-4 w-4" />Empty</Button>}
      </div>
      {trash.isLoading ? <Loading /> : !trash.data?.length ? <Empty icon={<Trash2 className="h-8 w-8" />} title="Recycle bin is empty" /> : (
        <ul className="divide-y">
          {trash.data.map((t) => (
            <li key={t.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              <FileIcon kind={t.is_dir ? "folder" : "file"} />
              <div className="min-w-0 flex-1"><div className="truncate">{t.name}</div><div className="truncate text-xs text-muted-foreground">{roots.find((r) => r.id === t.root_id)?.name}/{t.rel_path} · deleted {date(t.deleted_at)} by {t.deleted_by}</div></div>
              <Badge>{bytes(t.size)}</Badge>
              <Button size="sm" variant="outline" onClick={() => restore(t)}><RotateCcw className="h-4 w-4" />Restore</Button>
              <Button size="sm" variant="ghost" onClick={() => purge(t)} aria-label="Delete forever"><Trash2 className="h-4 w-4 text-danger" /></Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
