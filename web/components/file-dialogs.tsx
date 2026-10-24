"use client";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Download, Folder, History, Home, Pencil, RotateCcw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { api, downloadUrl, post, qs, type FileEntry, type Root } from "@/lib/api";
import { CLOUD } from "@/lib/cloud";
import { cloudDownload, cloudFileBlob } from "@/lib/cloud-transfer";
import { bytes, date } from "@/lib/format";
import { Button, Dialog, Empty, ErrorBox, Field, Input, Loading, Select, Spinner, Textarea, useToast } from "./ui";

const TEXT_KINDS = new Set(["text", "code"]);
const EXTRACT_EXT = new Set(["pdf", "docx", "xlsx", "xlsm", "csv", "tsv", "md", "txt", "json"]);

export function PreviewDialog({ file, onClose, canWrite, onSaved }: { file: FileEntry | null; onClose: () => void; canWrite: boolean; onSaved: () => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const isText = !!file && (TEXT_KINDS.has(file.kind) || EXTRACT_EXT.has(file.ext)) && file.kind !== "pdf";
  const text = useQuery({
    queryKey: ["preview-text", file?.root_id, file?.path, file?.modified],
    queryFn: () => api<string>(`/api/files/text?${qs({ root_id: file!.root_id, path: file!.path, max_chars: 500000 })}`),
    enabled: !!file && isText,
  });
  useEffect(() => { setEditing(false); }, [file]);
  const [cloudSrc, setCloudSrc] = useState<string>("");
  const media = !!file && ["image", "video", "audio", "pdf"].includes(file.kind);
  useEffect(() => {
    if (!CLOUD || !file || !media) return;
    let url = "";
    let alive = true;
    setCloudSrc("");
    cloudFileBlob(file.root_id, file.path).then(({ blob }) => {
      if (!alive) return;
      url = URL.createObjectURL(blob);
      setCloudSrc(url);
    }).catch((e) => toast("error", (e as Error).message));
    return () => { alive = false; if (url) URL.revokeObjectURL(url); };
  }, [file, media, toast]);
  if (!file) return null;
  const src = CLOUD ? cloudSrc : downloadUrl(file.root_id, file.path, true);
  const editable = canWrite && TEXT_KINDS.has(file.kind) && file.size < 5 * 1024 * 1024;
  const save = async () => {
    setSaving(true);
    try {
      await post("/api/files/write", { root_id: file.root_id, path: file.path, content: draft, overwrite: true });
      toast("success", "Saved. The previous version was kept in version history.");
      setEditing(false); onSaved();
    } catch (e) { toast("error", (e as Error).message); } finally { setSaving(false); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} wide title={file.name} description={`${bytes(file.size)} · modified ${date(file.modified)}`}
      footer={<>
        {editable && !editing && <Button variant="outline" onClick={() => { setDraft(text.data || ""); setEditing(true); }}><Pencil className="h-4 w-4" />Edit</Button>}
        {editing && <Button onClick={save} loading={saving}><Save className="h-4 w-4" />Save</Button>}
        {CLOUD
          ? <Button variant="outline" onClick={() => cloudDownload(file.root_id, file.path).catch((e) => toast("error", (e as Error).message))}><Download className="h-4 w-4" />Download</Button>
          : <a href={downloadUrl(file.root_id, file.path)}><Button variant="outline"><Download className="h-4 w-4" />Download</Button></a>}
      </>}>
      <div className="flex max-h-[70vh] min-h-[200px] items-center justify-center overflow-auto rounded-md bg-muted/50">
        {CLOUD && media && !cloudSrc ? <Spinner />
          : file.kind === "image" ? <img src={src} alt={file.name} className="max-h-[68vh] max-w-full object-contain" />
          : file.kind === "video" ? <video src={src} controls className="max-h-[68vh] max-w-full" />
          : file.kind === "audio" ? <audio src={src} controls className="w-full" />
          : file.kind === "pdf" ? <iframe src={src} title={file.name} className="h-[68vh] w-full rounded-md bg-white" />
          : isText ? (text.isLoading ? <Spinner /> : text.error ? <ErrorBox error={text.error} /> : editing
            ? <Textarea className="h-[66vh] font-mono text-xs" value={draft} onChange={(e) => setDraft(e.target.value)} />
            : <pre className="h-full w-full self-start whitespace-pre-wrap break-words p-4 font-mono text-xs">{text.data}</pre>)
          : <Empty title="No preview for this file type" description="Download the file to open it." />}
      </div>
    </Dialog>
  );
}

export function RenameDialog({ file, onClose, onDone }: { file: FileEntry | null; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { setName(file?.name || ""); setError(null); }, [file]);
  if (!file) return null;
  const submit = async () => {
    try { await post("/api/files/rename", { root_id: file.root_id, path: file.path, new_name: name }); onDone(); onClose(); }
    catch (e) { setError(e); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Rename ${file.is_dir ? "folder" : "file"}`}
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button onClick={submit} disabled={!name.trim() || name === file.name}>Rename</Button></>}>
      <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="space-y-3">
        <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} onFocus={(e) => {
          const dot = file.is_dir ? -1 : e.target.value.lastIndexOf(".");
          e.target.setSelectionRange(0, dot > 0 ? dot : e.target.value.length);
        }} />
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}

export function NewFolderDialog({ open, onClose, rootId, path, onDone }: { open: boolean; onClose: () => void; rootId: number; path: string; onDone: () => void }) {
  const [name, setName] = useState("New folder");
  const [error, setError] = useState<unknown>(null);
  const submit = async () => {
    try { await post("/api/files/mkdir", { root_id: rootId, path, name }); onDone(); onClose(); setName("New folder"); }
    catch (e) { setError(e); }
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="New folder"
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button onClick={submit} disabled={!name.trim()}>Create</Button></>}>
      <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="space-y-3">
        <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} onFocus={(e) => e.target.select()} />
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}

export function MoveDialog({ items, copy, roots, onClose, onDone }: { items: FileEntry[]; copy: boolean; roots: Root[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const writable = roots.filter((r) => r.access === "rw");
  const [rootId, setRootId] = useState<number>(items[0]?.root_id ?? writable[0]?.id);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const tree = useQuery({ queryKey: ["tree", rootId, path], queryFn: () => api<FileEntry[]>(`/api/files/tree?${qs({ root_id: rootId, path })}`), enabled: !!rootId });
  const moving = new Set(items.map((i) => `${i.root_id}:${i.path}`));
  const submit = async () => {
    setBusy(true);
    try {
      const r = await post<{ results: { ok: boolean; error?: string; path: string }[] }>("/api/files/transfer", {
        items: items.map((i) => ({ root_id: i.root_id, path: i.path })), dest_root_id: rootId, dest_dir: path, as_copy: copy,
      });
      const failed = r.results.filter((x) => !x.ok);
      if (failed.length) toast("error", `${failed.length} item(s) failed: ${failed[0].error}`);
      else toast("success", `${copy ? "Copied" : "Moved"} ${items.length} item(s)`);
      onDone(); onClose();
    } catch (e) { toast("error", (e as Error).message); } finally { setBusy(false); }
  };
  const crumbs = path ? path.split("/") : [];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`${copy ? "Copy" : "Move"} ${items.length} item${items.length > 1 ? "s" : ""}`}
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button onClick={submit} loading={busy}>{copy ? "Copy here" : "Move here"}</Button></>}>
      <div className="space-y-3">
        <Field label="Destination location">
          <Select className="w-full" value={rootId} onChange={(e) => { setRootId(Number(e.target.value)); setPath(""); }}>
            {writable.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </Select>
        </Field>
        <div className="flex flex-wrap items-center gap-1 text-sm">
          <button className="rounded p-1 hover:bg-muted" onClick={() => setPath("")}><Home className="h-4 w-4" /></button>
          {crumbs.map((c, i) => (<span key={i} className="flex items-center gap-1"><ChevronRight className="h-3 w-3 text-muted-foreground" />
            <button className="rounded px-1 hover:bg-muted" onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}>{c}</button></span>))}
        </div>
        <div className="h-64 overflow-y-auto rounded-md border">
          {tree.isLoading ? <Loading /> : tree.error ? <ErrorBox className="m-2" error={tree.error} /> : (
            <ul className="divide-y">
              {tree.data?.filter((f) => !moving.has(`${f.root_id}:${f.path}`)).map((f) => (
                <li key={f.path}><button className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted" onClick={() => setPath(f.path)}>
                  <Folder className="h-4 w-4 text-amber-500" />{f.name}</button></li>
              ))}
              {!tree.data?.length && <li className="p-3 text-sm text-muted-foreground">No sub-folders — items will go into this folder.</li>}
            </ul>
          )}
        </div>
      </div>
    </Dialog>
  );
}

type Version = { id: number; size: number; mtime: number; created_at: number; reason: string };
export function VersionsDialog({ file, onClose, canWrite, onDone }: { file: FileEntry | null; onClose: () => void; canWrite: boolean; onDone: () => void }) {
  const toast = useToast();
  const q = useQuery({ queryKey: ["versions", file?.root_id, file?.path], queryFn: () => api<Version[]>(`/api/files/versions?${qs({ root_id: file!.root_id, path: file!.path })}`), enabled: !!file });
  if (!file) return null;
  const restore = async (v: Version) => {
    try { await post(`/api/files/versions/${v.id}/restore`, { root_id: file.root_id }); toast("success", "Version restored (current file saved as a version)"); onDone(); q.refetch(); }
    catch (e) { toast("error", (e as Error).message); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={<span className="flex items-center gap-2"><History className="h-4 w-4" />Version history</span>} description={file.name}>
      {q.isLoading ? <Loading /> : q.data?.length ? (
        <ul className="divide-y rounded-md border">
          {q.data.map((v) => (
            <li key={v.id} className="flex items-center gap-3 px-3 py-2 text-sm">
              <div className="flex-1"><div>{date(v.mtime)}</div><div className="text-xs text-muted-foreground">{bytes(v.size)} · saved {date(v.created_at)} · {v.reason}</div></div>
              {!CLOUD && <a href={`/api/files/versions/${v.id}/download?${qs({ root_id: file.root_id })}`}><Button size="sm" variant="ghost"><Download className="h-4 w-4" /></Button></a>}
              {canWrite && <Button size="sm" variant="outline" onClick={() => restore(v)}><RotateCcw className="h-4 w-4" />Restore</Button>}
            </li>
          ))}
        </ul>
      ) : <Empty title="No previous versions" description="Versions are kept automatically when this file is overwritten (if versioning is on for the location)." />}
    </Dialog>
  );
}

export function ChecksumDialog({ file, onClose }: { file: FileEntry | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["checksum", file?.root_id, file?.path, file?.modified], queryFn: () => api<{ checksum: string }>(`/api/files/checksum?${qs({ root_id: file!.root_id, path: file!.path })}`), enabled: !!file });
  if (!file) return null;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="SHA-256 checksum" description={file.name}>
      {q.isLoading ? <Loading label="Hashing…" /> : q.error ? <ErrorBox error={q.error} /> : <code className="block break-all rounded-md bg-muted p-3 font-mono text-xs">{q.data?.checksum}</code>}
    </Dialog>
  );
}
