"use client";
import { useQuery } from "@tanstack/react-query";
import { Apple, KeyRound, Smartphone } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { TokenDialog } from "@/components/token-dialog";
import { TokensTable } from "@/components/tokens-table";
import { ConnectDashboard } from "@/components/connect-dashboard";
import { Badge, Button, Card, CardBody, CardHeader, Code, CopyBox, Notice, PageHeader } from "@/components/ui";

type Net = { public_url: string; request_base: string; secure_request: boolean; tailscale: { installed: boolean; running?: boolean; dns_name?: string; suggested_url?: string | null } };

export default function MobilePage() {
  const me = useMe();
  const net = useQuery({ queryKey: ["network"], queryFn: () => api<Net>("/api/system/network") });
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"android" | "ios">("android");
  const base = (net.data?.public_url || net.data?.tailscale.suggested_url || net.data?.request_base || "").replace(/\/$/, "");
  const dav = `${base}/dav/`;
  const httpsReady = base.startsWith("https://");

  return (
    <>
      <PageHeader title="Connect Phone & PC" description="Link your phone to this home PC: live connection checklist, QR sign-in, and file-manager setup."
        actions={<Button onClick={() => setOpen(true)}><KeyRound className="h-4 w-4" />New app password</Button>} />

      <ConnectDashboard />
      <h2 className="mb-3 mt-6 text-sm font-semibold">Use a file manager app (WebDAV)</h2>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Connection details" description="Same details for every WebDAV-capable app." />
          <CardBody className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-[140px_1fr] sm:items-center">
              <span className="text-sm text-muted-foreground">Server (WebDAV)</span><CopyBox value={dav} />
              <span className="text-sm text-muted-foreground">Username</span><CopyBox value={me.data?.user.username || ""} />
              <span className="text-sm text-muted-foreground">Password</span><span className="text-sm">An <b>app password</b> from this page (your account password is not accepted).</span>
            </div>
            {!net.data?.tailscale.running && <Notice tone="warning">Tailscale is not connected on this PC, so this address only works at home. Set up Tailscale under Settings → Network for access from anywhere.</Notice>}
            {net.data?.tailscale.running && !httpsReady && <Notice tone="warning">Enable HTTPS with <Code>tailscale serve --bg http://127.0.0.1:8787</Code> on the PC and save the https://…ts.net address in Settings. iOS apps and Windows require HTTPS for password sign-in.</Notice>}
            {httpsReady && <Notice tone="success">Encrypted HTTPS over your private Tailscale network. Only your signed-in devices can reach it.</Notice>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Before you start" />
          <CardBody className="space-y-2 text-sm">
            <p>1. Install <b>Tailscale</b> on the phone and sign in with the same account as the PC.</p>
            <p>2. Create an <b>app password</b> for this phone.</p>
            <p>3. Add the server in a file manager (below).</p>
            <p className="text-muted-foreground">Lost the phone? Revoke its app password — nothing else changes.</p>
          </CardBody>
        </Card>
      </div>

      <Card className="mt-4">
        <div className="flex border-b">
          {(["android", "ios"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)} className={cn("flex items-center gap-2 px-5 py-3 text-sm", tab === t ? "border-b-2 border-primary font-medium" : "text-muted-foreground")}>
              {t === "android" ? <Smartphone className="h-4 w-4" /> : <Apple className="h-4 w-4" />}{t === "android" ? "Android" : "iPhone & iPad"}
            </button>
          ))}
        </div>
        <CardBody className="space-y-5 text-sm leading-relaxed">
          {tab === "android" ? (
            <>
              <section>
                <h3 className="mb-1 font-semibold">Option A — file manager with WebDAV (recommended)</h3>
                <p className="text-muted-foreground">Apps such as <b>Solid Explorer</b>, <b>CX File Explorer</b>, <b>Total Commander</b> (WebDAV plugin) or <b>Material Files</b> can browse, open, upload, rename, move and delete files directly on the PC.</p>
                <ol className="mt-2 list-decimal space-y-1 pl-5">
                  <li>Open the app → <i>Add storage / Network / Remote</i> → <b>WebDAV</b>.</li>
                  <li>Server: <Code>{dav}</Code> (if the app asks separately: host <Code>{base.replace(/^https?:\/\//, "")}</Code>, path <Code>/dav/</Code>, {httpsReady ? "HTTPS on port 443" : "HTTP"}).</li>
                  <li>Username <Code>{me.data?.user.username}</Code>, password = the app password.</li>
                  <li>Each storage location appears as a folder.</li>
                </ol>
              </section>
              <section>
                <h3 className="mb-1 font-semibold">Option B — appear in Android’s system file picker</h3>
                <p className="text-muted-foreground">Android has no built-in WebDAV client. Apps that register a <i>Storage Access Framework document provider</i> make your PC show up in the system “Files” picker of every app (attach, save-as). Examples: <b>Solid Explorer</b> (enable “Show in system file picker”), <b>Round Sync</b> (rclone-based), or <b>CX File Explorer</b>. Support varies by app and Android version.</p>
              </section>
              <section>
                <h3 className="mb-1 font-semibold">Offline files & folder sync</h3>
                <p className="text-muted-foreground">Download individual files in the file manager for offline use, or use <b>FolderSync</b> (WebDAV account) to keep a phone folder (e.g. Camera) synced to the PC on a schedule.</p>
              </section>
            </>
          ) : (
            <>
              <Notice>Apple’s Files app connects natively to <b>SMB</b> servers only — it has no built-in WebDAV. Choose one of these supported methods.</Notice>
              <section>
                <h3 className="mb-1 font-semibold">Option A — WebDAV app that integrates with Files (recommended)</h3>
                <p className="text-muted-foreground">Apps such as <b>Documents by Readdle</b>, <b>FE File Explorer</b>, <b>WebDAV Navigator</b> or <b>Owlfiles</b> connect via WebDAV; several expose the connection inside the Files app as a location.</p>
                <ol className="mt-2 list-decimal space-y-1 pl-5">
                  <li>Install Tailscale on the iPhone and turn it on.</li>
                  <li>In the app: Add connection → <b>WebDAV</b> → URL <Code>{dav}</Code>.</li>
                  <li>Username <Code>{me.data?.user.username}</Code>, password = app password. {httpsReady ? <Badge tone="success">HTTPS ready</Badge> : <Badge tone="warning">needs HTTPS (tailscale serve)</Badge>}</li>
                </ol>
              </section>
              <section>
                <h3 className="mb-1 font-semibold">Option B — native Files app via Windows file sharing (SMB)</h3>
                <p className="text-muted-foreground">Share the same folders with Windows’ built-in file sharing (folder → Properties → Sharing). Then in Files: <i>⋯ → Connect to Server</i> → <Code>smb://{net.data?.tailscale.dns_name || "your-pc.tailnet.ts.net"}</Code>. Works over Tailscale; uses your Windows account. Note: SMB access is governed by Windows permissions, not by this dashboard.</p>
              </section>
              <section>
                <h3 className="mb-1 font-semibold">Option C — this web app</h3>
                <p className="text-muted-foreground">Open <Code>{base || "your server address"}</Code> in Safari and use <i>Share → Add to Home Screen</i>. Upload from Photos/Files, preview and download from any browser.</p>
              </section>
            </>
          )}
        </CardBody>
      </Card>

      <Card className="mt-4">
        <CardHeader title="Your devices" description="App passwords and device tokens. Revoke any you no longer use." />
        <CardBody className="p-0 pb-2"><TokensTable kinds={["webdav", "device", "api"]} /></CardBody>
      </Card>
      <TokenDialog kind="webdav" open={open} onOpenChange={setOpen} />
    </>
  );
}
