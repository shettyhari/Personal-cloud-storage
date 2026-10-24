"use client";
import { useQueryClient } from "@tanstack/react-query";
import { FolderSearch } from "lucide-react";
import { useState } from "react";
import { post } from "@/lib/api";
import { HostFolderPicker } from "./folder-picker";
import { Button, Dialog, ErrorBox, Field, Input, Switch, useToast } from "./ui";

export function AddRootDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [opts, setOpts] = useState({ read_only: false, webdav: true, ai_read: false, ai_write: false, versioning: true, index_content: true, create: false });
  const [picker, setPicker] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof opts) => (v: boolean) => setOpts((o) => ({ ...o, [k]: v, ...(k === "ai_read" && !v ? { ai_write: false } : {}) }));

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await post("/api/roots", { name: name || path.split(/[\\/]/).filter(Boolean).pop() || "Storage", path, ...opts });
      toast("success", "Storage location added. Indexing started in the background.");
      qc.invalidateQueries({ queryKey: ["roots"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      setPath(""); setName("");
      onOpenChange(false);
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange} title="Add storage location"
        description="Approve a folder on this PC for access from your devices. Nothing outside approved folders is ever reachable."
        footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button onClick={submit} loading={busy} disabled={!path}>Add location</Button></>}>
        <div className="space-y-4">
          <Field label="Folder">
            <div className="flex gap-2">
              <Input placeholder="D:\PersonalCloud" value={path} onChange={(e) => setPath(e.target.value)} />
              <Button variant="outline" onClick={() => setPicker(true)}><FolderSearch className="h-4 w-4" />Browse</Button>
            </div>
          </Field>
          <Field label="Display name" hint="Shown in the file explorer and as the folder name in WebDAV."><Input placeholder="e.g. Documents" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <div className="divide-y rounded-md border px-3">
            <Switch checked={opts.create} onCheckedChange={set("create")} label="Create the folder if it does not exist" />
            <Switch checked={opts.read_only} onCheckedChange={set("read_only")} label="Read-only" description="Nobody can change files here through the server." />
            <Switch checked={opts.webdav} onCheckedChange={set("webdav")} label="Available to mobile file managers (WebDAV)" />
            <Switch checked={opts.versioning} onCheckedChange={set("versioning")} label="Keep previous versions when files are overwritten" />
            <Switch checked={opts.index_content} onCheckedChange={set("index_content")} label="Index document contents for search" />
            <Switch checked={opts.ai_read} onCheckedChange={set("ai_read")} label="AI can read" description="Claude may search and read files here when connected." />
            <Switch checked={opts.ai_write} onCheckedChange={set("ai_write")} disabled={!opts.ai_read} label="AI can write" description="Create new files freely; moves, renames and deletes still need your approval." />
          </div>
          <ErrorBox error={error} />
        </div>
      </Dialog>
      <HostFolderPicker open={picker} onOpenChange={setPicker} onPick={(p) => { setPath(p); if (!name) setName(p.split(/[\\/]/).filter(Boolean).pop() || ""); }} />
    </>
  );
}
