"use client";
import { Cloud, Smartphone } from "lucide-react";
import { useEffect, useState } from "react";
import { api, post, setCsrf } from "@/lib/api";
import { Button, Card, ErrorBox } from "@/components/ui";

// Opened on the phone by scanning the QR code on the Connect page. Redeems the one-time code → signed in.
export default function PairPage() {
  const [code, setCode] = useState("");
  const [server, setServer] = useState("your home PC");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setCode(new URLSearchParams(location.search).get("code") || "");
    api<{ server_name: string }>("/api/setup/status").then((s) => setServer(s.server_name)).catch(() => undefined);
  }, []);

  const connect = async () => {
    setBusy(true); setError(null);
    try {
      const r = await post<{ csrf: string }>("/api/mobile/pair/redeem", { code });
      setCsrf(r.csrf);
      history.replaceState(null, "", "/pair");
      location.href = "/files";
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm p-6 text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-primary text-primary-foreground"><Cloud className="h-6 w-6" /></div>
        <h1 className="text-lg font-semibold">Connect this phone</h1>
        <p className="mt-1 text-sm text-muted-foreground">Sign this device in to <b>{server}</b>. It stays signed in until the session expires or you sign it out under Security.</p>
        {code ? (
          <Button size="lg" className="mt-6 w-full" onClick={connect} loading={busy}><Smartphone className="h-5 w-5" />Connect</Button>
        ) : (
          <p className="mt-6 text-sm text-danger">No pairing code in this link. Scan the QR code on the dashboard again.</p>
        )}
        <ErrorBox className="mt-4 text-left" error={error} />
        <p className="mt-6 text-xs text-muted-foreground">Tip: after connecting, use Share → <b>Add to Home Screen</b> to open it like an app.</p>
      </Card>
    </div>
  );
}
