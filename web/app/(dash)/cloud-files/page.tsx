"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, CloudUpload, Download, FolderPlus, Home, Pencil, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { sb } from "@/lib/cloud";
import { useCloudSession } from "@/lib/cloud-hooks";
import { bytes, date } from "@/lib/format";
import { FileIcon, kindOf } from "@/components/file-icon";
import { Button, Card, Dialog, Empty, ErrorBox, Input, Loading, Notice, PageHeader, useConfirm, useToast } from "@/components/ui";

const BUCKET = "cloud-files";
const MAX = 50 * 1024 * 1024;
type Item = { name: string; id: string | null; updated_at?: string; metadata?: { size?: number; mimetype?: string } | null };

export default function CloudFilesPage() {
  const session = useCloudSession();
  const uid = session.data?.userId;
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, node] = useConfirm();
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [newFolder, setNewFolder] = useState(false);
  const [renaming, setRenaming] = useState<Item | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const prefix = uid ? [uid, path].filter(Boolean).join("/") : "";
  const bucket = () => sb().storage.from(BUCKET);

  const list = useQuery({
    queryKey: ["cloud-files", prefix],
    enabled: !!uid,
    queryFn: async () => {
      const { data, error } = await bucket().list(prefix, { limit: 1000, sortBy: { column: "name", order: "asc" } });
      if (error) throw error;
      return ((data || []) as Item[]).filter((i) => i.name !== ".keep")
        .sort((a, b) => (a.id === null) === (b.id === null) ? a.name.localeCompare(b.name) : a.id === null ? -1 : 1);
    },
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["cloud-files"] });
  const full = (name: string) => `${prefix}/${name}`;

  const upload = async (files: FileList) => {
    for (const f of Array.from(files)) {
      if (f.size > MAX) { toast("error", `${f.name} is larger than 50 MB (the free-plan limit for Cloud Storage). Store it on a server instead.`); continue; }
      setBusy(`Uploading ${f.name}…`);
      const { error } = await bucket().upload(full(f.name), f, { upsert: false, contentType: f.type || undefined });
      if (error) toast("error", `${f.name}: ${error.message}`);
    }
    setBusy(null); refresh();
  };
  const download = async (i: Item) => {
    const { data, error } = await bucket().createSignedUrl(full(i.name), 120, { download: i.name });
    if (error) return toast("error", error.message);
    location.href = data.signedUrl;
  };
  const allUnder = async (p: string): Promise<string[]> => {
    const { data } = await bucket().list(p, { limit: 1000 });
    const out: string[] = [];
    for (const i of (data || []) as Item[]) {
      if (i.id === null) out.push(...(await allUnder(`${p}/${i.name}`))); else out.push(`${p}/${i.name}`);
    }
    return out;
  };
  const remove = async (i: Item) => {
    if (!(await confirm(`Delete “${i.name}”${i.id === null ? " and everything in it" : ""}?`, { danger: true, confirm: "Delete", body: "Cloud Storage has no recycle bin." }))) return;
    const paths = i.id === null ? await allUnder(full(i.name)) : [full(i.name)];
    const { error } = await bucket().remove(paths);
    if (error) toast("error", error.message); else refresh();
  };
  const makeFolder = async (name: string) => {
    const { error } = await bucket().upload(`${full(name)}/.keep`, new Blob([""]), { upsert: true });
    if (error) toast("error", error.message); else { setNewFolder(false); refresh(); }
  };
  const rename = async (i: Item, name: string) => {
    if (i.id === null) { toast("error", "Folders can't be renamed here yet; move their files instead."); return; }
    const { error } = await bucket().move(full(i.name), full(name));
    if (error) toast("error", error.message); else { setRenaming(null); refresh(); }
  };
  const crumbs = path ? path.split("/") : [];

  return (
    <>
      {node}
      <PageHeader title="Cloud Storage" description="Files kept in your private cloud bucket — available even when every server is off."
        actions={<>
          <Button variant="outline" onClick={() => setNewFolder(true)}><FolderPlus className="h-4 w-4" />New folder</Button>
          <Button onClick={() => input.current?.click()} loading={!!busy}><Upload className="h-4 w-4" />Upload</Button>
        </>} />
      <input ref={input} type="file" multiple hidden onChange={(e) => { if (e.target.files) upload(e.target.files); e.target.value = ""; }} />
      {busy && <Notice className="mb-3">{busy}</Notice>}
      <div className="mb-3 flex flex-wrap items-center gap-1 text-sm">
        <button className="rounded p-1 hover:bg-muted" onClick={() => setPath("")} aria-label="Top"><Home className="h-4 w-4" /></button>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1"><ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
            <button className="rounded px-1.5 hover:bg-muted" onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}>{c}</button></span>
        ))}
      </div>
      <Card className="overflow-hidden">
        {list.isLoading || !uid ? <Loading /> : list.error ? <ErrorBox className="m-4" error={list.error} /> : !list.data?.length ? (
          <Empty icon={<CloudUpload className="h-8 w-8" />} title="Nothing here yet" description="Upload files up to 50 MB each. Free plan: 1 GB total." />
        ) : (
          <ul className="divide-y">
            {list.data.map((i) => (
              <li key={i.name} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <FileIcon kind={i.id === null ? "folder" : kindOf(i.name)} />
                <button className="min-w-0 flex-1 truncate text-left hover:text-accent" onClick={() => i.id === null ? setPath([path, i.name].filter(Boolean).join("/")) : download(i)}>{i.name}</button>
                <span className="hidden w-24 text-right text-muted-foreground sm:block">{i.id === null ? "—" : bytes(i.metadata?.size)}</span>
                <span className="hidden w-36 text-right text-muted-foreground md:block">{i.updated_at ? date(Date.parse(i.updated_at) / 1000) : ""}</span>
                {i.id !== null && <Button size="icon" variant="ghost" aria-label="Download" onClick={() => download(i)}><Download className="h-4 w-4" /></Button>}
                {i.id !== null && <Button size="icon" variant="ghost" aria-label="Rename" onClick={() => setRenaming(i)}><Pencil className="h-4 w-4" /></Button>}
                <Button size="icon" variant="ghost" aria-label="Delete" onClick={() => remove(i)}><Trash2 className="h-4 w-4 text-danger" /></Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <NameDialog open={newFolder} title="New folder" initial="New folder" onClose={() => setNewFolder(false)} onSave={makeFolder} />
      {renaming && <NameDialog open title="Rename" initial={renaming.name} onClose={() => setRenaming(null)} onSave={(n) => rename(renaming, n)} />}
    </>
  );
}

function NameDialog({ open, title, initial, onClose, onSave }: { open: boolean; title: string; initial: string; onClose: () => void; onSave: (n: string) => void }) {
  const [name, setName] = useState(initial);
  const bad = !name.trim() || /[\\/]/.test(name);
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title={title}
      footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button disabled={bad} onClick={() => onSave(name.trim())}>Save</Button></>}>
      <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} onFocus={(e) => e.target.select()} />
    </Dialog>
  );
}
