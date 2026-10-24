"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Power, RotateCw, Stethoscope } from "lucide-react";
import { useEffect, useState } from "react";
import { api, patch, post } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { NetworkSetup } from "@/components/network-setup";
import { Badge, Button, Card, CardBody, CardHeader, Code, Dialog, ErrorBox, Field, Input, Loading, Notice, PageHeader, Switch, useToast } from "@/components/ui";

type Settings = Record<string, unknown>;
type Diag = { status: string; checks: { check: string; status: string; detail: string; fix: string }[] };

export default function SettingsPage() {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const isAdmin = me.data?.user.role === "admin";
  const s = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/api/settings") });
  const info = useQuery({ queryKey: ["info"], queryFn: () => api<{ version: string; data_dir: string | null; python: string }>("/api/system/info") });
  const [draft, setDraft] = useState<Settings>({});
  useEffect(() => { if (s.data) setDraft(s.data); }, [s.data]);
  if (!isAdmin) return <><PageHeader title="Settings" /><Notice>Only administrators can change server settings. You can change your password and 2FA under Security.</Notice></>;
  if (s.isLoading) return <Loading />;
  if (s.error) return <ErrorBox error={s.error} />;

  const save = async (keys: string[]) => {
    try {
      await patch("/api/settings", Object.fromEntries(keys.map((k) => [k, draft[k]])));
      toast("success", "Settings saved");
      qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["me"] });
    } catch (e) { toast("error", (e as Error).message); }
  };
  const num = (k: string) => <Input type="number" min={0} value={String(draft[k] ?? "")} onChange={(e) => setDraft({ ...draft, [k]: Number(e.target.value) })} />;
  const txt = (k: string, ph = "") => <Input placeholder={ph} value={String(draft[k] ?? "")} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />;
  const sw = (k: string, label: string, description?: string) => <Switch checked={Boolean(draft[k])} onCheckedChange={(v) => setDraft({ ...draft, [k]: v })} label={label} description={description} />;

  return (
    <>
      <PageHeader title="Settings" description={`Personal Cloud OS ${info.data?.version || ""}`} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader title="General" /><CardBody className="space-y-3">
          <Field label="Server name">{txt("server_name")}</Field>
          <Field label="Sign-out after inactivity (minutes)">{num("session_idle_minutes")}</Field>
          {sw("require_2fa_for_admin", "Remind administrators to use 2FA")}
          <Button variant="outline" onClick={() => save(["server_name", "session_idle_minutes", "require_2fa_for_admin"])}>Save</Button>
        </CardBody></Card>

        <Card><CardHeader title="Files, recycle bin & versions" description="Retention settings (separate from sync and backup)." /><CardBody className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Keep deleted items (days)">{num("trash_retention_days")}</Field>
            <Field label="Versions kept per file">{num("versions_keep")}</Field>
            <Field label="Skip versioning files larger than (MB)">{num("versions_max_file_mb")}</Field>
            <Field label="Re-index every (minutes)" hint="0 = only on demand">{num("index_interval_minutes")}</Field>
            <Field label="Max document size for content search (MB)">{num("index_max_content_mb")}</Field>
          </div>
          <Button variant="outline" onClick={() => save(["trash_retention_days", "versions_keep", "versions_max_file_mb", "index_interval_minutes", "index_max_content_mb"])}>Save</Button>
        </CardBody></Card>

        <Card className="lg:col-span-2"><CardHeader title="Network access" description="Who can reach this server. Loopback (this PC) is always allowed." /><CardBody className="space-y-3">
          <NetworkSetup />
          {sw("allow_tailscale", "Allow Tailscale devices (100.64.0.0/10)", "Recommended. Turn off only if you exclusively use this PC locally.")}
          <Field label="Extra allowed networks (CIDR, comma separated)" hint="Advanced — e.g. a WireGuard subnet like 10.8.0.0/24.">
            <Input value={((draft.extra_allowed_cidrs as string[]) || []).join(", ")} onChange={(e) => setDraft({ ...draft, extra_allowed_cidrs: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
          </Field>
          <Button variant="outline" onClick={() => save(["allow_tailscale", "extra_allowed_cidrs"])}>Save</Button>
        </CardBody></Card>

        <Card><CardHeader title="AI integration" /><CardBody className="space-y-3">
          {sw("mcp_enabled", "Enable MCP endpoint for AI assistants", "Turning this off immediately blocks every AI token.")}
          <Field label="Max characters an AI can read per request">{num("ai_max_read_chars")}</Field>
          <Field label="Approval requests expire after (hours)">{num("ai_approval_expiry_hours")}</Field>
          <div className="border-t pt-3">
            {sw("local_ai_enabled", "Enable local AI (Ollama)", "Summaries run on this PC; content is not sent to any external provider.")}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Local AI URL">{txt("local_ai_url", "http://127.0.0.1:11434")}</Field>
              <Field label="Model">{txt("local_ai_model", "llama3.2")}</Field>
            </div>
          </div>
          <Button variant="outline" onClick={() => save(["mcp_enabled", "ai_max_read_chars", "ai_approval_expiry_hours", "local_ai_enabled", "local_ai_url", "local_ai_model"])}>Save</Button>
        </CardBody></Card>

        <ServerCard dataDir={info.data?.data_dir || ""} />
      </div>
      <DiagnosticsCard />
    </>
  );
}

function ServerCard({ dataDir }: { dataDir: string }) {
  const [mode, setMode] = useState<"stop" | "restart" | null>(null);
  const [pw, setPw] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);
  const submit = async () => {
    setError(null);
    try { await post("/api/system/shutdown", { password: pw, restart: mode === "restart" }); setDone(true); }
    catch (e) { setError(e); }
  };
  return (
    <Card><CardHeader title="Server agent" /><CardBody className="space-y-3 text-sm">
      <div>Data folder: <Code>{dataDir}</Code></div>
      <p className="text-muted-foreground">Holds the database, logs, recycle bin, versions and the <Code>scripts</Code> folder for approved scripts.</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => { setMode("restart"); setDone(false); }}><RotateCw className="h-4 w-4" />Restart agent</Button>
        <Button variant="danger" onClick={() => { setMode("stop"); setDone(false); }}><Power className="h-4 w-4" />Stop server</Button>
      </div>
      <Dialog open={!!mode} onOpenChange={(o) => !o && setMode(null)} title={mode === "stop" ? "Stop the server?" : "Restart the server agent?"}
        description={mode === "stop" ? "All remote access, WebDAV and AI connections stop until you start it again on the PC (or at next sign-in if auto-start is enabled)." : "Connections drop for a few seconds while the agent restarts."}
        footer={done ? <Button onClick={() => setMode(null)}>Close</Button> : <><Button variant="outline" onClick={() => setMode(null)}>Cancel</Button><Button variant={mode === "stop" ? "danger" : "default"} onClick={submit} disabled={!pw}>Confirm</Button></>}>
        {done ? <Notice tone="success">{mode === "stop" ? "Server is shutting down." : "Restarting… reload the page in a few seconds."}</Notice> : <><Field label="Confirm with your password"><Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field><ErrorBox error={error} className="mt-2" /></>}
      </Dialog>
    </CardBody></Card>
  );
}

function DiagnosticsCard() {
  const [run, setRun] = useState(false);
  const q = useQuery({ queryKey: ["diag"], queryFn: () => api<Diag>("/api/system/diagnostics"), enabled: run });
  return (
    <Card className="mt-4">
      <CardHeader title="Diagnostics" description="Same checks as scripts\pcloud.ps1 doctor." action={<Button size="sm" variant="outline" onClick={() => { setRun(true); q.refetch(); }}><Stethoscope className="h-4 w-4" />Run checks</Button>} />
      <CardBody>
        {!run ? <p className="text-sm text-muted-foreground">Checks the database, storage folders, dashboard build, Tailscale, HTTPS and privilege level.</p> : q.isLoading ? <Loading /> : q.data && (
          <ul className="divide-y text-sm">
            {q.data.checks.map((c) => (
              <li key={c.check} className="flex items-start gap-3 py-2">
                <Badge tone={c.status === "ok" ? "success" : c.status === "warn" ? "warning" : "danger"}>{c.status}</Badge>
                <div className="min-w-0 flex-1"><div className="font-medium">{c.check}</div><div className="break-words text-xs text-muted-foreground">{c.detail}</div>{c.fix && c.status !== "ok" && <div className="text-xs">Fix: {c.fix}</div>}</div>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}
