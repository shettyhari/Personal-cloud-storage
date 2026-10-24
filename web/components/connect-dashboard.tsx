"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle, Laptop, QrCode, RefreshCw, Smartphone, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api, patch, post } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import { TokenDialog } from "./token-dialog";
import { Badge, Button, Card, CardBody, CardHeader, CopyBox, ErrorBox, Loading, Notice, Progress } from "./ui";

type Step = { id: string; label: string; ok: boolean | null; detail: string };
type Status = {
  steps: Step[]; ready: boolean; base_url: string; remote_access_seen: boolean;
  tailscale: { installed: boolean; running?: boolean; dns_name?: string };
  serve: { configured: boolean; url?: string | null };
  peers: { name: string; os: string; online: boolean; last_seen: string | null }[];
  devices: { id: number; name: string; kind: string; last_used: number | null; last_ip: string | null }[];
  mobile_sessions: { last_seen: number; ip: string; user_agent: string }[];
};
type Pair = { url: string; expires_in: number; qr_svg: string; https: boolean; base: string };

const isPhoneOs = (os: string) => ["ios", "android"].includes((os || "").toLowerCase());

export function ConnectDashboard() {
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ["mobile-status"], queryFn: () => api<Status>("/api/mobile/status"), refetchInterval: 5000 });
  const [pair, setPair] = useState<Pair | null>(null);
  const [left, setLeft] = useState(0);
  const [pairError, setPairError] = useState<unknown>(null);
  const [tokenOpen, setTokenOpen] = useState(false);

  useEffect(() => {
    if (!pair) return;
    const end = Date.now() + pair.expires_in * 1000;
    const t = setInterval(() => setLeft(Math.max(0, Math.round((end - Date.now()) / 1000))), 1000);
    setLeft(pair.expires_in);
    return () => clearInterval(t);
  }, [pair]);

  if (s.isLoading) return <Card className="mb-4"><Loading label="Checking the connection…" /></Card>;
  if (s.error || !s.data) return <ErrorBox className="mb-4" error={s.error} />;
  const st = s.data;
  const required = st.steps.slice(0, 5);
  const done = required.filter((x) => x.ok).length;
  const reachableBase = !/127\.0\.0\.1|localhost/.test(st.base_url);

  const makeQr = async () => {
    setPairError(null);
    try { setPair(await post<Pair>("/api/mobile/pair", { base_url: st.base_url })); } catch (e) { setPairError(e); }
  };
  const saveAddress = async (url: string) => {
    await patch("/api/settings", { public_url: url });
    qc.invalidateQueries({ queryKey: ["mobile-status"] }); qc.invalidateQueries({ queryKey: ["network"] });
  };

  const action = (x: Step) => {
    if (x.ok) return null;
    switch (x.id) {
      case "tailscale":
        return <a href="https://tailscale.com/download/windows" target="_blank" rel="noreferrer"><Button size="sm" variant="outline">Get Tailscale for Windows</Button></a>;
      case "https":
        return <CopyBox className="mt-2" value="tailscale serve --bg http://127.0.0.1:8787" />;
      case "address":
        return st.serve.url ? <Button size="sm" variant="outline" onClick={() => saveAddress(st.serve.url!)}>Save {st.serve.url}</Button> : null;
      case "phone":
        return (
          <div className="mt-2 flex flex-wrap gap-2">
            <a href="https://apps.apple.com/app/tailscale/id1470499037" target="_blank" rel="noreferrer"><Button size="sm" variant="outline">iPhone app</Button></a>
            <a href="https://play.google.com/store/apps/details?id=com.tailscale.ipn" target="_blank" rel="noreferrer"><Button size="sm" variant="outline">Android app</Button></a>
          </div>
        );
      case "signed_in":
        return <Button size="sm" variant="outline" onClick={makeQr}><QrCode className="h-4 w-4" />Show QR code</Button>;
      case "files_app":
        return <Button size="sm" variant="outline" onClick={() => setTokenOpen(true)}>New app password</Button>;
      default:
        return null;
    }
  };

  return (
    <>
      <div className="mb-4 grid gap-4 lg:grid-cols-5">
        {/* Checklist */}
        <Card className="lg:col-span-3">
          <CardHeader
            title={st.ready ? "Your phone can reach this PC from anywhere" : `${required.length - done} step${required.length - done === 1 ? "" : "s"} left to connect your phone`}
            description="Checked live every few seconds — complete the steps in order."
            action={<Button size="sm" variant="ghost" onClick={() => s.refetch()} aria-label="Re-check"><RefreshCw className={cn("h-4 w-4", s.isFetching && "animate-spin")} /></Button>} />
          <CardBody className="space-y-1 pt-3">
            <Progress value={(done / required.length) * 100} tone={st.ready ? "success" : "accent"} className="mb-3" />
            <ol>
              {st.steps.map((x, i) => (
                <li key={x.id} className="flex gap-3 border-b py-3 last:border-0">
                  <span className="mt-0.5">
                    {x.ok ? <CheckCircle2 className="h-5 w-5 text-success" />
                      : x.ok === false ? <XCircle className="h-5 w-5 text-danger" />
                        : <Circle className="h-5 w-5 text-muted-foreground" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium">{i + 1}. {x.label}{x.id === "files_app" && <span className="ml-1 font-normal text-muted-foreground">(optional)</span>}</div>
                    <div className="break-words text-xs text-muted-foreground">{x.detail}</div>
                    {action(x)}
                  </div>
                </li>
              ))}
            </ol>
          </CardBody>
        </Card>

        {/* QR pairing */}
        <Card className="lg:col-span-2">
          <CardHeader title="Sign in your phone with a QR code" description="Open the phone's camera and point it at the code. The phone opens your dashboard already signed in — no typing." />
          <CardBody className="flex flex-col items-center gap-3 pt-3 text-center">
            {!reachableBase && <Notice tone="warning" className="text-left">This address only works on the home PC itself. Finish steps 2–4 first so the QR code contains your private <b>https://…ts.net</b> address.</Notice>}
            {pair && left > 0 ? (
              <>
                <div className="rounded-xl border bg-white p-2" aria-label="Pairing QR code" dangerouslySetInnerHTML={{ __html: pair.qr_svg }} />
                <div className="text-xs text-muted-foreground">Valid for {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} · works once · phone must have Tailscale on</div>
                <details className="w-full text-left text-xs"><summary className="cursor-pointer text-muted-foreground">Can't scan? Copy the link</summary><CopyBox className="mt-1" value={pair.url} /></details>
                <Button size="sm" variant="ghost" onClick={makeQr}>New code</Button>
              </>
            ) : (
              <>
                <div className="flex h-48 w-48 items-center justify-center rounded-xl border border-dashed text-muted-foreground"><QrCode className="h-12 w-12" /></div>
                {pair && <div className="text-xs text-muted-foreground">That code expired.</div>}
                <Button onClick={makeQr}><QrCode className="h-4 w-4" />{pair ? "New QR code" : "Show QR code"}</Button>
              </>
            )}
            <ErrorBox error={pairError} />
          </CardBody>
        </Card>
      </div>

      {/* Devices */}
      <Card className="mb-4">
        <CardHeader title="Devices on your private network" description="From Tailscale: everything signed in to your account. Phones need it switched on to reach this PC." />
        <CardBody className="pt-3">
          {!st.tailscale.running ? <p className="text-sm text-muted-foreground">Connect Tailscale on this PC (step 2) to see your devices.</p>
            : !st.peers.length ? <p className="text-sm text-muted-foreground">No other devices yet. Install Tailscale on your phone and sign in with the same account.</p> : (
              <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {st.peers.map((p) => (
                  <li key={p.name} className="flex items-center gap-3 rounded-lg border p-3 text-sm">
                    {isPhoneOs(p.os) ? <Smartphone className="h-5 w-5 text-muted-foreground" /> : <Laptop className="h-5 w-5 text-muted-foreground" />}
                    <div className="min-w-0 flex-1"><div className="truncate font-medium">{p.name}</div><div className="text-xs capitalize text-muted-foreground">{p.os || "device"}</div></div>
                    <Badge tone={p.online ? "success" : "neutral"}>{p.online ? "online" : "offline"}</Badge>
                  </li>
                ))}
              </ul>
            )}
          {!!st.mobile_sessions.length && (
            <div className="mt-4 border-t pt-3 text-sm">
              <div className="mb-1 text-xs font-medium text-muted-foreground">Phones signed in to the dashboard</div>
              {st.mobile_sessions.slice(0, 5).map((m, i) => (
                <div key={i} className="flex gap-2 py-0.5"><Smartphone className="h-4 w-4 text-muted-foreground" /><span className="min-w-0 flex-1 truncate">{m.user_agent}</span><span className="text-xs text-muted-foreground">{ago(m.last_seen)}</span></div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>
      <TokenDialog kind="webdav" open={tokenOpen} onOpenChange={setTokenOpen} />
    </>
  );
}
