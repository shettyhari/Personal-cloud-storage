"use client";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Folder, HardDrive, Home } from "lucide-react";
import { useState } from "react";
import { api, qs } from "@/lib/api";
import { bytes } from "@/lib/format";
import { Button, Dialog, ErrorBox, Input, Loading } from "./ui";

type Browse = { path: string; parent: string | null; folders: { name: string; path: string; free?: number; total?: number }[]; shortcuts?: { name: string; path: string }[] };

/** Browse folders on the home PC (admin only) to pick a storage/backup location. */
export function HostFolderPicker({ open, onOpenChange, onPick, title = "Choose a folder on this PC" }: {
  open: boolean; onOpenChange: (o: boolean) => void; onPick: (path: string) => void; title?: string;
}) {
  const [path, setPath] = useState("");
  const [manual, setManual] = useState("");
  const q = useQuery({ queryKey: ["browse", path], queryFn: () => api<Browse>(`/api/fs/browse?${qs({ path })}`), enabled: open });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title}
      description="Only folders you add are shared. Whole drives and Windows system folders are blocked."
      footer={<>
        <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button disabled={!path && !manual} onClick={() => { onPick(manual || path); onOpenChange(false); }}>Use {manual ? "typed path" : "this folder"}</Button>
      </>}>
      <div className="mb-3 flex items-center gap-1 text-sm">
        <button className="rounded p-1 hover:bg-muted" onClick={() => setPath("")} aria-label="Drives"><Home className="h-4 w-4" /></button>
        {path && <><ChevronRight className="h-3 w-3 text-muted-foreground" /><span className="truncate font-mono text-xs">{path}</span></>}
      </div>
      <div className="h-72 overflow-y-auto rounded-md border">
        {q.isLoading ? <Loading /> : q.error ? <ErrorBox className="m-3" error={q.error} /> : (
          <ul className="divide-y">
            {q.data?.parent !== null && q.data?.parent !== undefined && (
              <li><button className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted" onClick={() => setPath(q.data!.parent || "")}>
                <Folder className="h-4 w-4 text-muted-foreground" /> ..</button></li>
            )}
            {q.data?.shortcuts?.map((s) => (
              <li key={s.path}><button className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted" onClick={() => setPath(s.path)}>
                <Folder className="h-4 w-4 text-accent" /> {s.name} <span className="ml-auto truncate font-mono text-xs text-muted-foreground">{s.path}</span></button></li>
            ))}
            {q.data?.folders.map((f) => (
              <li key={f.path}><button className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted" onClick={() => setPath(f.path)}>
                {path ? <Folder className="h-4 w-4 text-muted-foreground" /> : <HardDrive className="h-4 w-4 text-muted-foreground" />}
                <span className="truncate">{f.name}</span>
                {f.total ? <span className="ml-auto text-xs text-muted-foreground">{bytes(f.free)} free of {bytes(f.total)}</span> : null}
              </button></li>
            ))}
            {q.data && !q.data.folders.length && !q.data.shortcuts?.length && <li className="p-3 text-sm text-muted-foreground">No sub-folders</li>}
          </ul>
        )}
      </div>
      <div className="mt-3">
        <Input placeholder="…or type a path, e.g. D:\PersonalCloud" value={manual} onChange={(e) => setManual(e.target.value)} />
      </div>
    </Dialog>
  );
}
