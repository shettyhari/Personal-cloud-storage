"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, patch } from "@/lib/api";
import { Badge, Button, Code, CopyBox, Field, Input, Loading, Notice, Switch } from "./ui";

type NetworkInfo = { bind: string; public_url: string; allow_lan: boolean; tailscale: { installed: boolean; running?: boolean; dns_name?: string; ips?: string[]; suggested_url?: string | null } };

export function NetworkSetup() {
  const qc = useQueryClient();
  const net = useQuery({ queryKey: ["network"], queryFn: () => api<NetworkInfo>("/api/system/network") });
  const [url, setUrl] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  if (!net.data) return <Loading />;
  const ts = net.data.tailscale;
  const port = net.data.bind.split(":").pop();
  const save = async (changes: Record<string, unknown>) => {
    await patch("/api/settings", changes);
    qc.invalidateQueries({ queryKey: ["network"] });
    setSaved(true); setTimeout(() => setSaved(false), 1500);
  };
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        Tailscale: {!ts.installed ? <Badge tone="warning">not installed</Badge> : ts.running ? <Badge tone="success">connected as {ts.dns_name}</Badge> : <Badge tone="warning">installed, not signed in</Badge>}
      </div>
      {!ts.installed && (
        <Notice tone="warning">
          <ol className="list-decimal space-y-1 pl-4">
            <li>Install Tailscale on this PC: <a className="text-accent underline" href="https://tailscale.com/download/windows" target="_blank" rel="noreferrer">tailscale.com/download/windows</a> and sign in.</li>
            <li>Install Tailscale on your phone/laptop and sign in with the same account.</li>
            <li>Reload this page.</li>
          </ol>
        </Notice>
      )}
      {ts.running && (
        <div className="space-y-2 text-sm">
          <p>Give your devices an <b>HTTPS</b> address (needed by iPhone/Windows WebDAV clients). Run once in PowerShell on this PC:</p>
          <CopyBox value={`tailscale serve --bg http://127.0.0.1:${port}`} />
          <p className="text-muted-foreground">Then your private address is <Code>{ts.suggested_url || "https://<pc-name>.<tailnet>.ts.net"}</Code> — reachable only by your Tailscale devices. No router ports are opened.</p>
        </div>
      )}
      <Field label="Remote address (HTTPS)" hint="Used in setup instructions for phones and Claude.">
        <div className="flex gap-2">
          <Input placeholder={ts.suggested_url || "https://my-pc.tail1234.ts.net"} value={url ?? net.data.public_url ?? ""} onChange={(e) => setUrl(e.target.value)} />
          <Button variant="outline" onClick={() => save({ public_url: (url ?? net.data.public_url ?? "").trim() })}>{saved ? "Saved" : "Save"}</Button>
        </div>
      </Field>
      <Switch checked={net.data.allow_lan} onCheckedChange={(v) => save({ allow_lan: v })} label="Also allow devices on my home network (LAN)"
        description="Only matters if the server listens on all interfaces (PCLOUD_HOST=0.0.0.0). Tailscale is recommended instead." />
    </div>
  );
}

