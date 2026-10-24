"use client";
import { Cloud } from "lucide-react";
import { useEffect, useState } from "react";
import { api, post, setCsrf } from "@/lib/api";
import { Button, Card, ErrorBox, Field, Input } from "@/components/ui";
import { CloudLogin } from "@/components/cloud-login";
import { CLOUD } from "@/lib/cloud";

export default function LoginPage() {
  return CLOUD ? <CloudLogin /> : <LocalLogin />;
}

function LocalLogin() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [mfa, setMfa] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [server, setServer] = useState("Home PC");

  useEffect(() => {
    api<{ needs_admin: boolean; server_name: string }>("/api/setup/status")
      .then((s) => { if (s.needs_admin) location.href = "/setup"; setServer(s.server_name); })
      .catch((e) => setError(e));
  }, []);

  const finish = (csrf: string) => {
    setCsrf(csrf);
    const next = new URLSearchParams(location.search).get("next");
    location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      if (mfa) {
        const r = await post<{ csrf: string }>("/api/auth/mfa", { code });
        finish(r.csrf);
      } else {
        const r = await post<{ csrf?: string; mfa_required?: boolean }>("/api/auth/login", { username, password });
        if (r.mfa_required) setMfa(true);
        else if (r.csrf) finish(r.csrf);
      }
    } catch (err) { setError(err); } finally { setBusy(false); }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm p-6">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground"><Cloud className="h-5 w-5" /></div>
          <div><div className="font-semibold">Personal Cloud OS</div><div className="text-sm text-muted-foreground">Sign in to {server}</div></div>
        </div>
        <form onSubmit={submit} className="space-y-4">
          {!mfa ? (
            <>
              <Field label="Username"><Input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required /></Field>
              <Field label="Password"><Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
            </>
          ) : (
            <Field label="Authenticator code" hint="Enter the 6-digit code from your authenticator app.">
              <Input autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={code} onChange={(e) => setCode(e.target.value)} required />
            </Field>
          )}
          <ErrorBox error={error} />
          <Button className="w-full" type="submit" loading={busy}>{mfa ? "Verify" : "Sign in"}</Button>
        </form>
        <p className="mt-6 text-xs text-muted-foreground">Forgot your password? On the home PC run <code className="font-mono">scripts\pcloud.ps1 reset-password &lt;user&gt;</code>.</p>
      </Card>
    </div>
  );
}
