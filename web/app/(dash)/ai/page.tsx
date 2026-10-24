"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, KeyRound, ShieldAlert, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api, post, type Activity, type Root } from "@/lib/api";
import { ago, cn, date } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { TokenDialog } from "@/components/token-dialog";
import { TokensTable } from "@/components/tokens-table";
import { Badge, Button, Card, CardBody, CardHeader, Code, CopyBox, Empty, ErrorBox, Field, Input, Loading, Notice, PageHeader, Select, Table, Td, Textarea, Th, useToast } from "@/components/ui";

type Approval = { id: number; kind: string; summary: string; requested_by: string; status: string; created_at: number; decided_by: string | null; result: unknown; params: Record<string, unknown> };
type Conn = {
  mcp_enabled: boolean; mcp_url: string; ai_roots: Root[]; claude_desktop_local: unknown; claude_desktop_remote: unknown;
  claude_code: string; local_ai: { enabled: boolean; reachable?: boolean; models?: string[]; model?: string; error?: string };
};

export default function AiPage() {
  const me = useMe();
  const isAdmin = me.data?.user.role === "admin";
  const qc = useQueryClient();
  const toast = useToast();
  const [tokenOpen, setTokenOpen] = useState(false);
  const [tab, setTab] = useState<"desktop" | "remote" | "code">("remote");
  const conn = useQuery({ queryKey: ["ai-conn"], queryFn: () => api<Conn>("/api/ai/connection") });
  const approvals = useQuery({ queryKey: ["approvals"], queryFn: () => api<Approval[]>("/api/ai/approvals"), enabled: isAdmin, refetchInterval: 10000 });
  const history = useQuery({ queryKey: ["approvals-all"], queryFn: () => api<Approval[]>("/api/ai/approvals?status=all"), enabled: isAdmin });
  const activity = useQuery({ queryKey: ["ai-activity"], queryFn: () => api<Activity[]>("/api/ai/activity?limit=50"), refetchInterval: 15000 });

  const decide = async (a: Approval, approve: boolean) => {
    try {
      const r = await post<{ status: string; error?: string }>(`/api/ai/approvals/${a.id}`, { approve });
      toast(r.status === "failed" ? "error" : "success", r.status === "executed" ? "Approved and executed" : r.status === "rejected" ? "Rejected" : `Failed: ${r.error}`);
    } catch (e) { toast("error", (e as Error).message); }
    qc.invalidateQueries({ queryKey: ["approvals"] }); qc.invalidateQueries({ queryKey: ["approvals-all"] }); qc.invalidateQueries({ queryKey: ["approvals-count"] });
  };

  const c = conn.data;
  return (
    <>
      <PageHeader title="AI Workspace" description="Let Claude search, read and organize approved files on this PC — with you in control."
        actions={isAdmin && <Button onClick={() => setTokenOpen(true)}><KeyRound className="h-4 w-4" />Connect Claude</Button>} />

      {isAdmin && (
        <Card className="mb-4">
          <CardHeader title={<span className="flex items-center gap-2"><ShieldAlert className="h-4 w-4 text-warning" />Pending approvals</span>}
            description="Moves, renames, deletes, overwrites, bulk reorganizations and scripts requested by AI wait here. Nothing happens until you approve." />
          <CardBody>
            {approvals.isLoading ? <Loading /> : !approvals.data?.length ? <p className="text-sm text-muted-foreground">No pending requests.</p> : (
              <ul className="space-y-2">
                {approvals.data.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
                    <Badge tone="warning">{a.kind.replace("_", " ")}</Badge>
                    <div className="min-w-0 flex-1"><div className="text-sm">{a.summary}</div><div className="text-xs text-muted-foreground">requested by {a.requested_by} · {ago(a.created_at)}</div>
                      {a.kind === "bulk_move" && Array.isArray(a.params.moves) && (
                        <details className="mt-1 text-xs"><summary className="cursor-pointer text-muted-foreground">Show {(a.params.moves as unknown[]).length} moves</summary>
                          <ul className="mt-1 max-h-40 overflow-y-auto font-mono">{(a.params.moves as { path: string; dest_dir: string }[]).map((m, i) => <li key={i}>{m.path} → {m.dest_dir}/</li>)}</ul></details>
                      )}
                    </div>
                    <Button size="sm" variant="outline" onClick={() => decide(a, false)}><X className="h-4 w-4" />Reject</Button>
                    <Button size="sm" onClick={() => decide(a, true)}><Check className="h-4 w-4" />Approve</Button>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Connect Claude" description="The AI never has automatic access. You create a token, paste it into Claude, and can revoke it any time." />
          <CardBody className="space-y-4 text-sm">
            {conn.isLoading ? <Loading /> : conn.error ? <ErrorBox error={conn.error} /> : c && (
              <>
                {!c.mcp_enabled && <Notice tone="warning">The MCP endpoint is disabled in Settings — all AI access is currently blocked.</Notice>}
                <div><div className="mb-1 text-xs font-medium text-muted-foreground">MCP endpoint (Streamable HTTP, Bearer token auth)</div><CopyBox value={c.mcp_url} /></div>
                <div className="flex gap-1 border-b">
                  {([["remote", "Claude Desktop (any computer)"], ["desktop", "Claude Desktop on this PC"], ["code", "Claude Code"]] as const).map(([k, l]) => (
                    <button key={k} onClick={() => setTab(k)} className={cn("px-3 py-2", tab === k ? "border-b-2 border-primary font-medium" : "text-muted-foreground")}>{l}</button>
                  ))}
                </div>
                {tab === "remote" && <>
                  <p>Works from your laptop anywhere through Tailscale. Requires Node.js. In Claude Desktop: <i>Settings → Developer → Edit Config</i>, add this to <Code>claude_desktop_config.json</Code>, replace the token, and restart Claude:</p>
                  <CopyBox multiline value={JSON.stringify(c.claude_desktop_remote, null, 2)} />
                </>}
                {tab === "desktop" && <>
                  <p>Runs the MCP server locally over stdio (no network). Add to <Code>claude_desktop_config.json</Code> on this PC:</p>
                  <CopyBox multiline value={JSON.stringify(c.claude_desktop_local, null, 2)} />
                </>}
                {tab === "code" && <><p>Run in a terminal:</p><CopyBox value={c.claude_code} /></>}
                <p className="text-muted-foreground">Then ask, for example: <i>“Find all marketing reports from October, compare their performance, create a summary, and save it in my Marketing folder.”</i></p>
                <Notice>Claude.ai web/mobile “custom connectors” must reach a public internet URL. This server stays private on your Tailscale network by design, so use Claude Desktop or Claude Code (above).</Notice>
              </>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="What AI can access" action={isAdmin && <Link className="text-sm text-accent" href="/storage">Change</Link>} />
          <CardBody className="space-y-2 text-sm">
            {c?.ai_roots.length ? c.ai_roots.map((r) => (
              <div key={r.id} className="flex items-center gap-2"><Bot className="h-4 w-4 text-accent" /><span className="flex-1">{r.name}</span><Badge tone={r.ai_write ? "warning" : "neutral"}>{r.ai_write ? "read + create" : "read only"}</Badge></div>
            )) : <p className="text-muted-foreground">No locations allow AI access. Enable “AI can read” on a storage location.</p>}
            <div className="border-t pt-3 text-xs text-muted-foreground">
              Always allowed (in AI-readable locations): list, search, read, analyze. <br />
              Allowed with “AI can write”: create folders, save new documents. <br />
              Always needs your approval: move, rename, delete, overwrite, bulk reorganize, run scripts.
            </div>
          </CardBody>
        </Card>
      </div>

      {c?.local_ai.enabled && <LocalAi roots={me.data?.roots || []} status={c.local_ai} />}

      {isAdmin && (
        <Card className="mt-4">
          <CardHeader title="Connected AI assistants" />
          <CardBody className="p-0 pb-2"><TokensTable kinds={["mcp"]} /></CardBody>
        </Card>
      )}

      <Card className="mt-4">
        <CardHeader title="AI activity log" description="Every file operation performed by an AI assistant." />
        {activity.isLoading ? <Loading /> : !activity.data?.length ? <Empty icon={<Bot className="h-6 w-6" />} title="No AI activity yet" /> : (
          <Table><thead><tr><Th>Time</Th><Th>Assistant</Th><Th>Action</Th><Th>Target</Th><Th>Status</Th></tr></thead><tbody>
            {activity.data.map((a) => (
              <tr key={a.id}><Td className="whitespace-nowrap">{date(a.ts)}</Td><Td>{a.actor}</Td><Td className="font-mono text-xs">{a.action}</Td><Td className="max-w-xs truncate">{a.target}</Td>
                <Td><Badge tone={a.status === "ok" ? "success" : a.status === "pending" ? "warning" : "danger"}>{a.status}</Badge></Td></tr>
            ))}
          </tbody></Table>
        )}
      </Card>

      {isAdmin && !!history.data?.filter((h) => h.status !== "pending").length && (
        <Card className="mt-4">
          <CardHeader title="Approval history" />
          <Table><thead><tr><Th>Requested</Th><Th>Action</Th><Th>Status</Th><Th>Decided by</Th></tr></thead><tbody>
            {history.data.filter((h) => h.status !== "pending").slice(0, 30).map((h) => (
              <tr key={h.id}><Td className="whitespace-nowrap">{date(h.created_at)}</Td><Td>{h.summary}</Td><Td><Badge tone={h.status === "executed" ? "success" : h.status === "rejected" || h.status === "expired" ? "neutral" : "danger"}>{h.status}</Badge></Td><Td>{h.decided_by || "—"}</Td></tr>
            ))}
          </tbody></Table>
        </Card>
      )}
      <TokenDialog kind="mcp" open={tokenOpen} onOpenChange={setTokenOpen} />
    </>
  );
}

function LocalAi({ roots, status }: { roots: Root[]; status: Conn["local_ai"] }) {
  const [rootId, setRootId] = useState(roots[0]?.id);
  const [path, setPath] = useState("");
  const [instructions, setInstructions] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async () => {
    setBusy(true); setError(null); setResult("");
    try { setResult((await post<{ summary: string }>("/api/ai/local/summarize", { root_id: rootId, path, instructions })).summary); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <Card className="mt-4">
      <CardHeader title={<span className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-accent" />Local AI (private)</span>}
        description={status.reachable ? `Using ${status.model} on this PC — document contents never leave your computer.` : `Local model not reachable: ${status.error || "start Ollama"}`} />
      <CardBody className="grid gap-3 md:grid-cols-[180px_1fr]">
        <Field label="Location"><Select className="w-full" value={rootId} onChange={(e) => setRootId(Number(e.target.value))}>{roots.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</Select></Field>
        <Field label="Document path"><Input placeholder="Reports/october.pdf" value={path} onChange={(e) => setPath(e.target.value)} /></Field>
        <Field label="Instructions (optional)" className="md:col-span-2"><Textarea rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="Summarize key numbers and risks" /></Field>
        <div className="md:col-span-2"><Button onClick={run} loading={busy} disabled={!path || !status.reachable}>Summarize locally</Button></div>
        <ErrorBox error={error} className="md:col-span-2" />
        {result && <pre className="whitespace-pre-wrap rounded-md bg-muted p-3 text-sm md:col-span-2">{result}</pre>}
      </CardBody>
    </Card>
  );
}
