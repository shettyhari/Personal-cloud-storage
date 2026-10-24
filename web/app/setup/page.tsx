"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Cloud, FolderPlus, HardDrive, KeyRound, Network, Smartphone, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";
import { api, patch, post, setCsrf, type Root } from "@/lib/api";
import { cn } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { HostFolderPicker } from "@/components/folder-picker";
import { AddRootDialog } from "@/components/root-form";
import { TokenDialog } from "@/components/token-dialog";
import { NetworkSetup } from "@/components/network-setup";
import { Badge, Button, Card, Code, CopyBox, ErrorBox, Field, Input, Loading, Notice, Switch } from "@/components/ui";

const STEPS = [
  { title: "Administrator", icon: UserPlus },
  { title: "Storage folders", icon: FolderPlus },
  { title: "Network access", icon: Network },
  { title: "AI integration", icon: Bot },
  { title: "Mobile access", icon: Smartphone },
  { title: "Backups", icon: HardDrive },
];

export default function SetupPage() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["setup-status"], queryFn: () => api<{ needs_admin: boolean; setup_complete: boolean }>("/api/setup/status") });
  const me = useMe();
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (status.data && !status.data.needs_admin && step === 0 && me.data) setStep(1);
    if (status.data && !status.data.needs_admin && me.isError) location.href = "/login?next=/setup";
  }, [status.data, me.data, me.isError, step]);

  const finish = async () => {
    await post("/api/setup/complete");
    await qc.invalidateQueries({ queryKey: ["me"] });
    location.href = "/";
  };

  if (status.isLoading) return <Loading />;
  return (
    <div className="min-h-screen bg-muted/40 px-4 py-8">
      <div className="mx-auto max-w-4xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground"><Cloud className="h-5 w-5" /></div>
          <div><h1 className="text-lg font-semibold">Set up Personal Cloud OS</h1><p className="text-sm text-muted-foreground">Turn this PC into your private cloud. You can change everything later.</p></div>
        </div>
        <div className="grid gap-6 md:grid-cols-[220px_1fr]">
          <ol className="flex gap-2 overflow-x-auto md:flex-col">
            {STEPS.map((s, i) => (
              <li key={s.title} className={cn("flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm",
                i === step ? "bg-card font-medium shadow-sm ring-1 ring-border" : "text-muted-foreground")}>
                <span className={cn("flex h-6 w-6 items-center justify-center rounded-full text-xs",
                  i < step ? "bg-success text-white" : i === step ? "bg-primary text-primary-foreground" : "bg-muted")}>{i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}</span>
                {s.title}
              </li>
            ))}
          </ol>
          <Card className="p-6">
            {step === 0 && <AdminStep onDone={() => { qc.invalidateQueries({ queryKey: ["me"] }); setStep(1); }} />}
            {step === 1 && <StorageStep />}
            {step === 2 && <NetworkStep />}
            {step === 3 && <AiStep />}
            {step === 4 && <MobileStep />}
            {step === 5 && <BackupStep />}
            {step > 0 && (
              <div className="mt-8 flex justify-between border-t pt-4">
                <Button variant="outline" onClick={() => setStep(step - 1)} disabled={step === 1}>Back</Button>
                {step < STEPS.length - 1
                  ? <Button onClick={() => setStep(step + 1)}>Continue</Button>
                  : <Button variant="accent" onClick={finish}>Finish setup</Button>}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function AdminStep({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [serverName, setServerName] = useState("Home PC");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setCode(new URLSearchParams(location.search).get("code") || ""); }, []);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirm) { setError(new Error("Passwords do not match")); return; }
    setBusy(true); setError(null);
    try {
      const r = await post<{ csrf: string }>("/api/setup/admin", { setup_token: code.trim(), username, password, server_name: serverName });
      setCsrf(r.csrf);
      onDone();
    } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      <div><h2 className="font-semibold">Create the administrator</h2><p className="text-sm text-muted-foreground">This account controls the server. Use a strong, unique password.</p></div>
      <Field label="Setup code" hint={<>Printed in the server window, and saved as <Code>setup-token.txt</Code> in the data folder. Proves you are at this PC.</>}>
        <Input value={code} onChange={(e) => setCode(e.target.value)} required />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Server name"><Input value={serverName} onChange={(e) => setServerName(e.target.value)} /></Field>
        <Field label="Username"><Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required pattern="[A-Za-z0-9_.\-]{3,40}" /></Field>
        <Field label="Password" hint="At least 10 characters with mixed case and digits, or a 14+ character passphrase."><Input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
        <Field label="Confirm password"><Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required /></Field>
      </div>
      <ErrorBox error={error} />
      <Button type="submit" loading={busy}>Create administrator</Button>
    </form>
  );
}

function StorageStep() {
  const roots = useQuery({ queryKey: ["roots"], queryFn: () => api<Root[]>("/api/roots") });
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-4">
      <div><h2 className="font-semibold">Approve storage folders</h2><p className="text-sm text-muted-foreground">Only the folders you add here are accessible remotely, from mobile, or by AI. For example <Code>D:\PersonalCloud</Code>, <Code>D:\Documents</Code>, <Code>E:\Media</Code>.</p></div>
      <ul className="divide-y rounded-md border">
        {roots.data?.map((r) => (
          <li key={r.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
            <HardDrive className="h-4 w-4 text-muted-foreground" />
            <div className="min-w-0 flex-1"><div className="font-medium">{r.name}</div><div className="truncate font-mono text-xs text-muted-foreground">{r.path}</div></div>
            {r.ai_read && <Badge tone="accent">AI</Badge>}{r.read_only && <Badge>read-only</Badge>}
          </li>
        ))}
        {!roots.data?.length && <li className="p-4 text-sm text-muted-foreground">No folders yet.</li>}
      </ul>
      <Button variant="outline" onClick={() => setOpen(true)}><FolderPlus className="h-4 w-4" />Add folder</Button>
      <AddRootDialog open={open} onOpenChange={setOpen} />
    </div>
  );
}

function NetworkStep() {
  return (
    <div className="space-y-4">
      <div><h2 className="font-semibold">Secure remote access</h2><p className="text-sm text-muted-foreground">Your PC is never exposed to the public internet. Devices reach it over Tailscale, an encrypted WireGuard network that works across networks without port forwarding.</p></div>
      <NetworkSetup />
    </div>
  );
}

function AiStep() {
  const [open, setOpen] = useState(false);
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api<Record<string, unknown>>("/api/settings") });
  const qc = useQueryClient();
  return (
    <div className="space-y-4">
      <div><h2 className="font-semibold">AI integration (Claude via MCP)</h2><p className="text-sm text-muted-foreground">Claude only gets access after you create a token, and only to folders where you enabled “AI can read”. Moves, renames and deletes always wait for your approval.</p></div>
      <Switch checked={Boolean(settings.data?.mcp_enabled)} onCheckedChange={async (v) => { await patch("/api/settings", { mcp_enabled: v }); qc.invalidateQueries({ queryKey: ["settings"] }); }}
        label="Enable the MCP endpoint" description="You can disable this at any time to cut off all AI access instantly." />
      <Button variant="outline" onClick={() => setOpen(true)}><KeyRound className="h-4 w-4" />Create AI token now</Button>
      <p className="text-sm text-muted-foreground">Or skip — the AI Workspace page has step-by-step instructions for Claude Desktop and Claude Code.</p>
      <TokenDialog kind="mcp" open={open} onOpenChange={setOpen} />
    </div>
  );
}

function MobileStep() {
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-4">
      <div><h2 className="font-semibold">Phones and tablets</h2><p className="text-sm text-muted-foreground">Native file managers connect via WebDAV using an app password (never your real password). Create one per device so you can revoke a lost phone individually.</p></div>
      <Button variant="outline" onClick={() => setOpen(true)}><Smartphone className="h-4 w-4" />Create WebDAV app password</Button>
      <p className="text-sm text-muted-foreground">Detailed Android and iPhone instructions are on the Mobile Access page.</p>
      <TokenDialog kind="webdav" open={open} onOpenChange={setOpen} />
    </div>
  );
}

function BackupStep() {
  const roots = useQuery({ queryKey: ["roots"], queryFn: () => api<Root[]>("/api/roots") });
  const [dest, setDest] = useState("");
  const [picker, setPicker] = useState(false);
  const [encrypted, setEncrypted] = useState(true);
  const [result, setResult] = useState<{ recovery_key: string | null } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const create = async () => {
    setError(null);
    try {
      setResult(await post<{ recovery_key: string | null }>("/api/backups", { name: "Daily backup", root_ids: roots.data?.map((r) => r.id) || [], destination: dest, encrypted, interval_hours: 24, keep_snapshots: 30 }));
    } catch (e) { setError(e); }
  };
  return (
    <div className="space-y-4">
      <div><h2 className="font-semibold">Backups</h2><p className="text-sm text-muted-foreground">Sync is not a backup: deletions and ransomware spread through sync. Backups keep dated, read-only snapshots you can restore from. Use a second drive (e.g. <Code>E:\Backups</Code>).</p></div>
      {result ? (
        <div className="space-y-3">
          <Notice tone="success">Daily backup created. It runs automatically every 24 hours.</Notice>
          {result.recovery_key && <><Notice tone="warning">Save this recovery key in a password manager or print it. Without it, encrypted backups cannot be restored on another PC.</Notice><CopyBox value={result.recovery_key} /></>}
        </div>
      ) : (
        <>
          <Field label="Backup destination">
            <div className="flex gap-2"><Input placeholder="E:\Backups" value={dest} onChange={(e) => setDest(e.target.value)} /><Button variant="outline" onClick={() => setPicker(true)}>Browse</Button></div>
          </Field>
          <Switch checked={encrypted} onCheckedChange={setEncrypted} label="Encrypt backups (AES-256-GCM)" />
          <ErrorBox error={error} />
          <Button variant="outline" onClick={create} disabled={!dest || !roots.data?.length}>Create daily backup</Button>
          {!roots.data?.length && <p className="text-xs text-muted-foreground">Add a storage folder first.</p>}
        </>
      )}
      <HostFolderPicker open={picker} onOpenChange={setPicker} onPick={setDest} title="Choose backup destination" />
    </div>
  );
}
