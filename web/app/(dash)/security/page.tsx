"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, ShieldCheck, UserPlus } from "lucide-react";
import { useState } from "react";
import { api, del, patch, post, type Root } from "@/lib/api";
import { ago, date } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { TokenDialog } from "@/components/token-dialog";
import { TokensTable } from "@/components/tokens-table";
import {
  Badge, Button, Card, CardBody, CardHeader, Code, CopyBox, Dialog, ErrorBox, Field, Input, Loading, Notice, PageHeader, Select, Switch,
  Table, Td, Th, useConfirm, useToast,
} from "@/components/ui";

type Session = { id: string; created_at: number; last_seen: number; ip: string; user_agent: string; current: boolean };
type User = { id: number; username: string; role: string; totp_enabled: boolean; disabled: boolean; created_at: number; permissions: { root_id: number; can_write: boolean }[] };

export default function SecurityPage() {
  const me = useMe();
  const isAdmin = me.data?.user.role === "admin";
  const [tokenOpen, setTokenOpen] = useState(false);
  return (
    <>
      <PageHeader title="Security" description="Your account, sessions, devices and users." />
      <div className="grid gap-4 lg:grid-cols-2">
        <PasswordCard />
        <TwoFactorCard />
      </div>
      <SessionsCard />
      <Card className="mt-4">
        <CardHeader title="All tokens & app passwords" description="WebDAV app passwords, device/API tokens and AI tokens. Stored only as SHA-256 hashes."
          action={<Button size="sm" variant="outline" onClick={() => setTokenOpen(true)}><KeyRound className="h-4 w-4" />API token</Button>} />
        <CardBody className="p-0 pb-2"><TokensTable kinds={["webdav", "device", "api", "mcp"]} allUsers={isAdmin} /></CardBody>
      </Card>
      {isAdmin && <UsersCard roots={me.data?.roots || []} selfId={me.data!.user.id} />}
      <Card className="mt-4">
        <CardHeader title={<span className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-success" />Built-in protections</span>} />
        <CardBody className="grid gap-x-8 gap-y-1 text-sm text-muted-foreground sm:grid-cols-2">
          <p>• Only approved folders are reachable; symlink/junction escapes blocked</p>
          <p>• Passwords hashed with scrypt; tokens stored as hashes</p>
          <p>• Sign-in rate limiting and temporary account lockout</p>
          <p>• HttpOnly, SameSite=Strict session cookies + CSRF tokens</p>
          <p>• Requests only accepted from this PC and your Tailscale network</p>
          <p>• Deletions go to the recycle bin; overwrites keep versions</p>
          <p>• AI destructive actions require your approval</p>
          <p>• No shell / PowerShell exposed; only registered scripts run</p>
          <p>• Secrets protected with Windows DPAPI</p>
          <p>• Full audit log of user, device and AI activity</p>
        </CardBody>
      </Card>
      <TokenDialog kind="device" open={tokenOpen} onOpenChange={setTokenOpen} />
    </>
  );
}

function PasswordCard() {
  const toast = useToast();
  const [cur, setCur] = useState(""); const [next, setNext] = useState(""); const [conf, setConf] = useState("");
  const [error, setError] = useState<unknown>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null);
    if (next !== conf) { setError(new Error("New passwords do not match")); return; }
    try { await post("/api/auth/password", { current_password: cur, new_password: next }); toast("success", "Password changed. Other sessions were signed out."); setCur(""); setNext(""); setConf(""); }
    catch (e2) { setError(e2); }
  };
  return (
    <Card><CardHeader title="Change password" /><CardBody>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Current password"><Input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="New password"><Input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required /></Field>
          <Field label="Confirm"><Input type="password" autoComplete="new-password" value={conf} onChange={(e) => setConf(e.target.value)} required /></Field>
        </div>
        <ErrorBox error={error} />
        <Button type="submit" variant="outline">Update password</Button>
      </form>
    </CardBody></Card>
  );
}

function TwoFactorCard() {
  const me = useMe();
  const qc = useQueryClient();
  const toast = useToast();
  const [setup, setSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [error, setError] = useState<unknown>(null);
  const enabled = me.data?.user.totp_enabled;
  const begin = async () => { setError(null); try { setSetup(await post("/api/auth/2fa/begin")); } catch (e) { setError(e); } };
  const enable = async () => { setError(null); try { await post("/api/auth/2fa/enable", { code }); toast("success", "Two-factor authentication enabled"); setSetup(null); setCode(""); qc.invalidateQueries({ queryKey: ["me"] }); } catch (e) { setError(e); } };
  const disable = async () => { setError(null); try { await post("/api/auth/2fa/disable", { password: pw, code }); toast("success", "Two-factor authentication disabled"); setPw(""); setCode(""); qc.invalidateQueries({ queryKey: ["me"] }); } catch (e) { setError(e); } };
  return (
    <Card><CardHeader title="Two-factor authentication" description="Require a code from an authenticator app (Google Authenticator, 1Password, Authy…) at sign-in."
      action={<Badge tone={enabled ? "success" : "neutral"}>{enabled ? "on" : "off"}</Badge>} />
      <CardBody className="space-y-3">
        {enabled ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2"><Field label="Password"><Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field><Field label="Current code"><Input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></Field></div>
            <Button variant="outline" onClick={disable}>Turn off</Button>
          </>
        ) : setup ? (
          <>
            <p className="text-sm">Add this key in your authenticator app (choose “enter a setup key”, time-based):</p>
            <CopyBox value={setup.secret} />
            <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">otpauth link</summary><CopyBox className="mt-1" value={setup.uri} /></details>
            <Field label="Enter the 6-digit code to confirm"><Input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></Field>
            <Button onClick={enable} disabled={code.length < 6}>Enable</Button>
          </>
        ) : <Button variant="outline" onClick={begin}>Set up two-factor authentication</Button>}
        <ErrorBox error={error} />
        <p className="text-xs text-muted-foreground">Lost your authenticator? On the PC run <Code>scripts\pcloud.ps1 disable-2fa &lt;user&gt;</Code>.</p>
      </CardBody>
    </Card>
  );
}

function SessionsCard() {
  const q = useQuery({ queryKey: ["sessions"], queryFn: () => api<Session[]>("/api/auth/sessions") });
  const revoke = async (s: Session) => { await del(`/api/auth/sessions/${s.id}`); q.refetch(); };
  const revokeOthers = async () => { await post("/api/auth/sessions/revoke-others"); q.refetch(); };
  return (
    <Card className="mt-4">
      <CardHeader title="Active sessions" description="Sessions expire automatically after inactivity." action={<Button size="sm" variant="outline" onClick={revokeOthers}>Sign out other sessions</Button>} />
      {q.isLoading ? <Loading /> : (
        <Table><thead><tr><Th>Device</Th><Th>IP</Th><Th>Signed in</Th><Th>Last active</Th><Th /></tr></thead><tbody>
          {q.data?.map((s) => (
            <tr key={s.id}><Td className="max-w-sm"><div className="truncate text-xs" title={s.user_agent}>{s.user_agent || "Unknown"}</div>{s.current && <Badge tone="success">this device</Badge>}</Td>
              <Td className="font-mono text-xs">{s.ip}</Td><Td className="whitespace-nowrap">{date(s.created_at)}</Td><Td>{ago(s.last_seen)}</Td>
              <Td className="text-right">{!s.current && <Button size="sm" variant="outline" onClick={() => revoke(s)}>Sign out</Button>}</Td></tr>
          ))}
        </tbody></Table>
      )}
    </Card>
  );
}

function UsersCard({ roots, selfId }: { roots: Root[]; selfId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [confirm, node] = useConfirm();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/api/users") });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [form, setForm] = useState({ username: "", password: "", role: "user" });
  const [error, setError] = useState<unknown>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["users"] });
  const create = async () => { setError(null); try { await post("/api/users", form); setAdding(false); setForm({ username: "", password: "", role: "user" }); refresh(); } catch (e) { setError(e); } };
  const update = async (u: User, changes: Record<string, unknown>) => { try { await patch(`/api/users/${u.id}`, changes); refresh(); } catch (e) { toast("error", (e as Error).message); } };
  const remove = async (u: User) => { if (await confirm(`Delete user ${u.username}?`, { danger: true, confirm: "Delete" })) { await del(`/api/users/${u.id}`); refresh(); } };
  return (
    <Card className="mt-4">
      {node}
      <CardHeader title="Users & folder permissions" description="Administrators can access every location. Other users only see locations you grant." action={<Button size="sm" variant="outline" onClick={() => setAdding(true)}><UserPlus className="h-4 w-4" />Add user</Button>} />
      {users.isLoading ? <Loading /> : (
        <Table><thead><tr><Th>User</Th><Th>Role</Th><Th>Access</Th><Th>2FA</Th><Th>Status</Th><Th /></tr></thead><tbody>
          {users.data?.map((u) => (
            <tr key={u.id}>
              <Td className="font-medium">{u.username}</Td>
              <Td><Select value={u.role} disabled={u.id === selfId} onChange={(e) => update(u, { role: e.target.value })}><option value="admin">admin</option><option value="user">user</option><option value="readonly">read-only</option></Select></Td>
              <Td className="text-xs">{u.role === "admin" ? "All locations" : u.permissions.length ? u.permissions.map((p) => `${roots.find((r) => r.id === p.root_id)?.name}${p.can_write ? " (rw)" : " (r)"}`).join(", ") : <span className="text-muted-foreground">none</span>}</Td>
              <Td>{u.totp_enabled ? <Badge tone="success">on</Badge> : <Badge>off</Badge>}</Td>
              <Td>{u.disabled ? <Badge tone="danger">disabled</Badge> : <Badge tone="success">active</Badge>}</Td>
              <Td className="whitespace-nowrap text-right">
                {u.role !== "admin" && <Button size="sm" variant="ghost" onClick={() => setEditing(u)}>Permissions</Button>}
                {u.id !== selfId && <Button size="sm" variant="ghost" onClick={() => update(u, { disabled: !u.disabled })}>{u.disabled ? "Enable" : "Disable"}</Button>}
                {u.id !== selfId && <Button size="sm" variant="ghost" onClick={() => remove(u)}>Delete</Button>}
              </Td>
            </tr>
          ))}
        </tbody></Table>
      )}
      <Dialog open={adding} onOpenChange={setAdding} title="Add user" footer={<><Button variant="outline" onClick={() => setAdding(false)}>Cancel</Button><Button onClick={create}><Plus className="h-4 w-4" />Create</Button></>}>
        <div className="space-y-3">
          <Field label="Username"><Input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></Field>
          <Field label="Initial password"><Input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <Field label="Role"><Select className="w-full" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option value="user">User</option><option value="readonly">Read-only</option><option value="admin">Administrator</option></Select></Field>
          <ErrorBox error={error} />
        </div>
      </Dialog>
      {editing && <PermissionsDialog user={editing} roots={roots} onClose={() => { setEditing(null); refresh(); }} />}
    </Card>
  );
}

function PermissionsDialog({ user, roots, onClose }: { user: User; roots: Root[]; onClose: () => void }) {
  const [perms, setPerms] = useState<Record<number, "none" | "r" | "rw">>(Object.fromEntries(roots.map((r) => {
    const p = user.permissions.find((x) => x.root_id === r.id);
    return [r.id, p ? (p.can_write ? "rw" : "r") : "none"];
  })));
  const save = async () => {
    await patch(`/api/users/${user.id}`, { permissions: Object.entries(perms).filter(([, v]) => v !== "none").map(([k, v]) => ({ root_id: Number(k), can_write: v === "rw" })) });
    onClose();
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Folder access for ${user.username}`} footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button onClick={save}>Save</Button></>}>
      <ul className="divide-y rounded-md border">
        {roots.map((r) => (
          <li key={r.id} className="flex items-center gap-3 px-3 py-2 text-sm"><span className="flex-1">{r.name}</span>
            <Select value={perms[r.id]} onChange={(e) => setPerms({ ...perms, [r.id]: e.target.value as "none" | "r" | "rw" })}><option value="none">No access</option><option value="r">Read</option><option value="rw">Read & write</option></Select></li>
        ))}
      </ul>
    </Dialog>
  );
}
void Notice; void Switch;
