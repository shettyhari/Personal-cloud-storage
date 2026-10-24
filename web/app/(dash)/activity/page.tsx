"use client";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { api, qs, type Activity } from "@/lib/api";
import { date } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { Badge, Button, Card, Empty, ErrorBox, Input, Loading, PageHeader, Select, Table, Td, Th } from "@/components/ui";

export default function ActivityPage() {
  const me = useMe();
  const [actor, setActor] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const list = useInfiniteQuery({
    queryKey: ["activity", actor, status, search],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => api<Activity[]>(`/api/activity?${qs({ actor_type: actor, status, q: search, before: pageParam, limit: 100 })}`),
    getNextPageParam: (last) => (last.length === 100 ? last[last.length - 1].ts : undefined),
    refetchInterval: 15000,
  });
  const rows = list.data?.pages.flat() || [];
  return (
    <>
      <PageHeader title="Activity Logs" description="Audit trail of sign-ins, file operations, device and AI access."
        actions={me.data?.user.role === "admin" && <a href="/api/activity/export.csv"><Button variant="outline"><Download className="h-4 w-4" />Export CSV</Button></a>} />
      <Card className="mb-3 flex flex-wrap items-center gap-2 p-3">
        <Select value={actor} onChange={(e) => setActor(e.target.value)} aria-label="Actor"><option value="">All actors</option><option value="user">Users</option><option value="device">Devices (WebDAV)</option><option value="ai">AI assistants</option><option value="system">System</option></Select>
        <Select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">Any status</option><option value="ok">ok</option><option value="failed">failed</option><option value="denied">denied</option><option value="error">error</option><option value="pending">pending</option></Select>
        <form className="flex flex-1 gap-2" onSubmit={(e) => { e.preventDefault(); setSearch(q); }}><Input placeholder="Filter by action, file or user…" value={q} onChange={(e) => setQ(e.target.value)} /><Button variant="outline" type="submit">Filter</Button></form>
      </Card>
      <Card>
        {list.isLoading ? <Loading /> : list.error ? <ErrorBox className="m-4" error={list.error} /> : !rows.length ? <Empty title="No activity" /> : (
          <>
            <Table><thead><tr><Th>Time</Th><Th>Actor</Th><Th>Action</Th><Th>Target</Th><Th>Status</Th><Th>IP</Th></tr></thead><tbody>
              {rows.map((a) => (
                <tr key={a.id}>
                  <Td className="whitespace-nowrap">{date(a.ts)}</Td>
                  <Td><Badge tone={a.actor_type === "ai" ? "accent" : "neutral"}>{a.actor_type}</Badge> <span className="text-sm">{a.actor}</span></Td>
                  <Td className="font-mono text-xs">{a.action}</Td>
                  <Td className="max-w-md"><div className="truncate" title={a.target || ""}>{a.target}</div>{a.details && <div className="truncate text-xs text-muted-foreground" title={JSON.stringify(a.details)}>{JSON.stringify(a.details)}</div>}</Td>
                  <Td><Badge tone={a.status === "ok" ? "success" : a.status === "pending" ? "warning" : "danger"}>{a.status}</Badge></Td>
                  <Td className="font-mono text-xs text-muted-foreground">{a.ip}</Td>
                </tr>
              ))}
            </tbody></Table>
            {list.hasNextPage && <div className="p-3 text-center"><Button variant="outline" onClick={() => list.fetchNextPage()} loading={list.isFetchingNextPage}>Load more</Button></div>}
          </>
        )}
      </Card>
    </>
  );
}
