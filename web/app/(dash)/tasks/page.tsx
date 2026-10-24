"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, Play, ScrollText, X } from "lucide-react";
import { useState } from "react";
import { api, post, type Job } from "@/lib/api";
import { date, duration } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { Badge, Button, Card, CardBody, CardHeader, Dialog, Empty, ErrorBox, Field, Input, Loading, PageHeader, Progress, Select, Table, Td, Th, useToast } from "@/components/ui";

const TONE: Record<string, "success" | "danger" | "warning" | "accent" | "neutral"> = {
  succeeded: "success", failed: "danger", interrupted: "warning", cancelled: "neutral", running: "accent", queued: "neutral",
};

const TEMPLATES: { type: string; label: string; fields: { key: string; label: string; placeholder?: string; list?: boolean }[] }[] = [
  { type: "inventory", label: "Generate file inventory (CSV)", fields: [{ key: "path", label: "Folder", placeholder: "Projects" }] },
  { type: "find_duplicates", label: "Find duplicate files", fields: [{ key: "path", label: "Folder", placeholder: "(whole location)" }] },
  { type: "search_dir", label: "Search a large directory", fields: [{ key: "path", label: "Folder" }, { key: "pattern", label: "Name pattern", placeholder: "*.pdf" }, { key: "contains", label: "Containing text (optional)" }] },
  { type: "compress", label: "Compress to ZIP", fields: [{ key: "paths", label: "Paths (comma separated)", list: true }, { key: "dest_dir", label: "Save in folder" }, { key: "archive_name", label: "Archive name", placeholder: "archive.zip" }] },
  { type: "extract", label: "Extract ZIP/TAR archive", fields: [{ key: "path", label: "Archive path" }, { key: "dest_dir", label: "Extract into folder" }] },
  { type: "batch_extract_text", label: "Batch: extract text from PDFs/DOCX/XLSX", fields: [{ key: "path", label: "Source folder" }, { key: "dest_dir", label: "Output folder" }] },
  { type: "convert", label: "Convert file format", fields: [{ key: "path", label: "File path" }, { key: "target", label: "Target format", placeholder: "csv / xlsx / txt" }] },
  { type: "checksums", label: "Generate SHA-256 checksums", fields: [{ key: "path", label: "Folder or file" }] },
  { type: "hash_file", label: "Checksum a large file", fields: [{ key: "path", label: "File path" }] },
];

export default function TasksPage() {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState("");
  const [logJob, setLogJob] = useState<Job | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const jobs = useQuery({ queryKey: ["jobs", status], queryFn: () => api<Job[]>(`/api/jobs?limit=200${status ? `&status=${status}` : ""}`), refetchInterval: 3000 });
  const cancel = async (j: Job) => { await post(`/api/jobs/${j.id}/cancel`); toast("info", "Cancelling…"); qc.invalidateQueries({ queryKey: ["jobs"] }); };

  return (
    <>
      <PageHeader title="Background Tasks" description="Approved processing jobs run on the PC even when your browser is closed."
        actions={<><Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter"><option value="">All</option>{Object.keys(TONE).map((s) => <option key={s}>{s}</option>)}</Select>
          {me.data?.user.role !== "readonly" && <Button onClick={() => setNewOpen(true)}><Play className="h-4 w-4" />New task</Button>}</>} />
      <Card>
        {jobs.isLoading ? <Loading /> : jobs.error ? <ErrorBox className="m-4" error={jobs.error} /> : !jobs.data?.length ? <Empty icon={<ListChecks className="h-8 w-8" />} title="No tasks yet" /> : (
          <Table>
            <thead><tr><Th>Task</Th><Th>Status</Th><Th>Progress</Th><Th>Started by</Th><Th>Created</Th><Th>Duration</Th><Th /></tr></thead>
            <tbody>
              {jobs.data.map((j) => (
                <tr key={j.id}>
                  <Td><div className="font-medium">#{j.id} {j.title}</div><div className="max-w-sm truncate text-xs text-muted-foreground">{j.message}</div></Td>
                  <Td><Badge tone={TONE[j.status] || "neutral"}>{j.status}</Badge></Td>
                  <Td className="w-40">{j.status === "running" || j.status === "queued" ? <Progress value={j.progress * 100} /> : j.status === "succeeded" ? "100%" : "—"}</Td>
                  <Td>{j.created_by}<div className="text-xs text-muted-foreground">{j.source}</div></Td>
                  <Td className="whitespace-nowrap">{date(j.created_at)}</Td>
                  <Td>{j.started_at ? duration((j.finished_at || Date.now() / 1000) - j.started_at) : "—"}</Td>
                  <Td className="whitespace-nowrap text-right">
                    <Button size="sm" variant="ghost" onClick={() => setLogJob(j)}><ScrollText className="h-4 w-4" />Details</Button>
                    {["running", "queued"].includes(j.status) && <Button size="sm" variant="outline" onClick={() => cancel(j)}><X className="h-4 w-4" />Cancel</Button>}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {logJob && <JobDetails job={logJob} onClose={() => setLogJob(null)} />}
      <NewTask open={newOpen} onOpenChange={setNewOpen} />
    </>
  );
}

function JobDetails({ job, onClose }: { job: Job; onClose: () => void }) {
  const log = useQuery({ queryKey: ["job-log", job.id], queryFn: () => api<string>(`/api/jobs/${job.id}/log`), refetchInterval: 3000 });
  const fresh = useQuery({ queryKey: ["job", job.id], queryFn: () => api<Job>(`/api/jobs/${job.id}`), refetchInterval: 3000 });
  const j = fresh.data || job;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} wide title={`#${j.id} ${j.title}`} description={`${j.status} · ${j.message || ""}`}>
      <div className="space-y-3">
        <div><div className="mb-1 text-xs font-medium text-muted-foreground">Parameters</div><pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(j.params, null, 2)}</pre></div>
        {j.result !== null && j.result !== undefined && <div><div className="mb-1 text-xs font-medium text-muted-foreground">Result</div><pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(j.result, null, 2)}</pre></div>}
        <div><div className="mb-1 text-xs font-medium text-muted-foreground">Output log</div><pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono text-xs">{log.data || "(empty)"}</pre></div>
      </div>
    </Dialog>
  );
}

function NewTask({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const roots = me.data?.roots || [];
  const [type, setType] = useState(TEMPLATES[0].type);
  const [rootId, setRootId] = useState<number | undefined>(undefined);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const tpl = TEMPLATES.find((t) => t.type === type)!;
  const submit = async () => {
    setError(null);
    const params: Record<string, unknown> = { root_id: rootId ?? roots[0]?.id };
    for (const f of tpl.fields) {
      const v = (values[f.key] || "").trim();
      if (f.list) params[f.key] = v.split(",").map((s) => s.trim()).filter(Boolean);
      else if (v) params[f.key] = v;
    }
    try {
      const r = await post<{ id: number }>("/api/jobs", { type, params });
      toast("success", `Task #${r.id} started`);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      onOpenChange(false); setValues({});
    } catch (e) { setError(e); }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Start a background task" description="Paths are relative to the chosen storage location."
      footer={<><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button onClick={submit}>Start</Button></>}>
      <div className="space-y-3">
        <Field label="Task"><Select className="w-full" value={type} onChange={(e) => { setType(e.target.value); setValues({}); }}>{TEMPLATES.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}</Select></Field>
        <Field label="Storage location"><Select className="w-full" value={rootId ?? roots[0]?.id} onChange={(e) => setRootId(Number(e.target.value))}>{roots.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</Select></Field>
        {tpl.fields.map((f) => (
          <Field key={f.key} label={f.label}><Input placeholder={f.placeholder} value={values[f.key] || ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} /></Field>
        ))}
        <ErrorBox error={error} />
      </div>
    </Dialog>
  );
}
void CardBody; void CardHeader;
