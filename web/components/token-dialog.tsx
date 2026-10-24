"use client";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { post } from "@/lib/api";
import { Button, CopyBox, Dialog, ErrorBox, Field, Input, Notice, Switch } from "./ui";

export type CreatedToken = { id: number; token: string; username: string; webdav_url: string; mcp_url: string };

const LABELS: Record<string, { title: string; desc: string; placeholder: string }> = {
  webdav: { title: "New WebDAV app password", desc: "Used by phone/tablet file managers. Revoke it any time if a device is lost.", placeholder: "e.g. Pixel 9 – Solid Explorer" },
  mcp: { title: "Connect an AI assistant", desc: "Creates an MCP token for Claude. It can only see locations with “AI can read” enabled.", placeholder: "e.g. Claude Desktop on laptop" },
  device: { title: "New device / API token", desc: "For scripts or apps calling the REST API with a Bearer token.", placeholder: "e.g. Backup script" },
};

export function TokenDialog({ kind, open, onOpenChange, onCreated }: {
  kind: "webdav" | "mcp" | "device"; open: boolean; onOpenChange: (o: boolean) => void; onCreated?: (t: CreatedToken) => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [readOnly, setReadOnly] = useState(false);
  const [expires, setExpires] = useState("");
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const l = LABELS[kind];

  const close = (o: boolean) => {
    if (!o) { setCreated(null); setName(""); setError(null); setReadOnly(false); setExpires(""); }
    onOpenChange(o);
  };
  const create = async () => {
    setBusy(true); setError(null);
    try {
      const t = await post<CreatedToken>("/api/tokens", { name, kind, read_only: readOnly, expires_days: expires ? Number(expires) : null });
      setCreated(t);
      onCreated?.(t);
      qc.invalidateQueries({ queryKey: ["tokens"] });
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onOpenChange={close} title={created ? "Copy your secret now" : l.title} description={created ? undefined : l.desc}
      footer={created ? <Button onClick={() => close(false)}>Done</Button> : <>
        <Button variant="outline" onClick={() => close(false)}>Cancel</Button>
        <Button onClick={create} loading={busy} disabled={!name.trim()}>Create</Button>
      </>}>
      {created ? (
        <div className="space-y-3">
          <Notice tone="warning">This secret is shown only once. Store it in the app / password manager now.</Notice>
          {kind === "webdav" && <>
            <Field label="Server address"><CopyBox value={created.webdav_url} /></Field>
            <Field label="Username"><CopyBox value={created.username} /></Field>
          </>}
          {kind === "mcp" && <Field label="MCP endpoint"><CopyBox value={created.mcp_url} /></Field>}
          <Field label={kind === "webdav" ? "App password" : "Token"}><CopyBox value={created.token} /></Field>
        </div>
      ) : (
        <div className="space-y-4">
          <Field label="Name"><Input autoFocus placeholder={l.placeholder} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} /></Field>
          {kind !== "mcp" && <Switch checked={readOnly} onCheckedChange={setReadOnly} label="Read-only" description="This device can browse and download but not change files." />}
          <Field label="Expires after (days)" hint="Leave empty for no expiry."><Input type="number" min={1} max={3650} value={expires} onChange={(e) => setExpires(e.target.value)} /></Field>
          <ErrorBox error={error} />
        </div>
      )}
    </Dialog>
  );
}
