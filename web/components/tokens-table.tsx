"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, KeyRound, Smartphone } from "lucide-react";
import { api, del, type Token } from "@/lib/api";
import { ago, date } from "@/lib/format";
import { Badge, Button, Empty, Loading, Table, Td, Th, useConfirm } from "./ui";

export function TokensTable({ kinds, allUsers }: { kinds: string[]; allUsers?: boolean }) {
  const qc = useQueryClient();
  const [confirm, node] = useConfirm();
  const q = useQuery({ queryKey: ["tokens", allUsers], queryFn: () => api<Token[]>(`/api/tokens${allUsers ? "?all_users=true" : ""}`) });
  const rows = (q.data || []).filter((t) => kinds.includes(t.kind));
  const revoke = async (t: Token) => {
    if (!(await confirm(`Revoke “${t.name}”?`, { body: "The device or assistant using it is disconnected immediately.", danger: true, confirm: "Revoke" }))) return;
    await del(`/api/tokens/${t.id}`);
    qc.invalidateQueries({ queryKey: ["tokens"] });
  };
  if (q.isLoading) return <Loading />;
  if (!rows.length) return <Empty icon={<KeyRound className="h-6 w-6" />} title="None yet" />;
  return (
    <>
      {node}
      <Table>
        <thead><tr><Th>Name</Th>{allUsers && <Th>User</Th>}<Th>Type</Th><Th>Created</Th><Th>Last used</Th><Th>Status</Th><Th /></tr></thead>
        <tbody>
          {rows.map((t) => {
            const expired = t.expires_at && t.expires_at < Date.now() / 1000;
            return (
              <tr key={t.id}>
                <Td><div className="flex items-center gap-2">{t.kind === "mcp" ? <Bot className="h-4 w-4 text-accent" /> : <Smartphone className="h-4 w-4 text-muted-foreground" />}<span>{t.name}</span></div><div className="font-mono text-xs text-muted-foreground">{t.prefix}…</div></Td>
                {allUsers && <Td>{t.username}</Td>}
                <Td><Badge>{t.kind === "mcp" ? "AI (MCP)" : t.kind}</Badge>{t.read_only ? <Badge className="ml-1">read-only</Badge> : null}</Td>
                <Td className="whitespace-nowrap">{date(t.created_at)}</Td>
                <Td className="whitespace-nowrap">{ago(t.last_used)}{t.last_ip && <div className="text-xs text-muted-foreground">{t.last_ip}</div>}</Td>
                <Td>{t.revoked ? <Badge tone="danger">revoked</Badge> : expired ? <Badge tone="warning">expired</Badge> : <Badge tone="success">active</Badge>}</Td>
                <Td className="text-right">{!t.revoked && <Button size="sm" variant="outline" onClick={() => revoke(t)}>Revoke</Button>}</Td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </>
  );
}
