# Remote access, HTTPS, firewall & networking

The agent listens on `127.0.0.1:8787` by default — **only this PC can connect**. To reach it from your phone
or laptop on any network, use Tailscale (a WireGuard-based private network). No router ports are opened and the
PC is never exposed to the public internet.

## Recommended: Tailscale + `tailscale serve` (HTTPS)

1. Install Tailscale on the PC: <https://tailscale.com/download/windows>, sign in.
2. Install Tailscale on your phone/laptop, sign in with the **same account** (or share the device).
3. In the Tailscale admin console → DNS, make sure **MagicDNS** and **HTTPS certificates** are enabled.
4. On the PC, in a normal PowerShell window:

   ```powershell
   tailscale serve --bg http://127.0.0.1:8787
   tailscale serve status        # shows https://<pc-name>.<tailnet>.ts.net
   ```

5. Dashboard → Settings → Network access → **Remote address**: paste `https://<pc-name>.<tailnet>.ts.net`.
6. Optional: in `.env` set `PCLOUD_SECURE_COOKIES=true` once you always use the HTTPS address (enables
   Secure cookies + HSTS), then `scripts\pcloud.ps1 restart`.

Now `https://<pc-name>.<tailnet>.ts.net` works from any of your Tailscale devices, on any network, with a valid
certificate. `tailscale serve` (not `funnel`) is **tailnet-only**; nothing is published to the internet.

`tailscale serve --bg` persists across reboots. Undo with `tailscale serve reset`.

### Why HTTPS matters

- iOS WebDAV apps and Windows' WebDAV client refuse password authentication over plain HTTP.
- Even though Tailscale already encrypts traffic end-to-end, HTTPS lets browsers treat the site as secure.

## Alternative: direct Tailscale IP (no `serve`)

Set `PCLOUD_HOST=0.0.0.0` in `.env` and restart. Windows Firewall will prompt — allow **Private** networks only.
Devices then use `http://100.x.y.z:8787`. The agent's IP allow-list (Settings → Network) still rejects any client
that is not loopback, Tailscale (100.64.0.0/10, fd7a:115c:a1e0::/48) or a network you add. HTTP only, so some
mobile clients will refuse to log in — prefer `tailscale serve`.

## Alternative: your own WireGuard

If you run WireGuard yourself (e.g. on your router), set `PCLOUD_HOST=0.0.0.0`, then add your WireGuard subnet
(e.g. `10.8.0.0/24`) under Settings → Network → *Extra allowed networks*. Put a TLS reverse proxy (Caddy) in front
if you need HTTPS.

## Home network (LAN) only

Settings → Network → *Also allow devices on my home network* + `PCLOUD_HOST=0.0.0.0`. Works only at home.

## Firewall summary

| Setup | Inbound firewall rule needed | Router port forwarding |
|---|---|---|
| Default (127.0.0.1) + `tailscale serve` | none | none |
| `PCLOUD_HOST=0.0.0.0` | allow `python.exe` on Private networks (Windows prompts) | none |
| RDP over Tailscale | Windows enables RDP rule when you turn RDP on | none — never forward 3389 |

## When direct connections fail (relay)

Tailscale automatically falls back to its encrypted DERP relays when two devices cannot connect directly (strict
NAT, CGNAT, hotel Wi-Fi). Traffic stays end-to-end encrypted with WireGuard; relays cannot read it. Throughput is
lower than a direct connection. For a self-hosted relay you can run your own DERP server
(<https://tailscale.com/kb/1118/custom-derp-servers>) or use Headscale; no changes to Personal Cloud OS are needed.

## Keeping the PC reachable

- Windows Settings → System → Power → **Sleep: Never** (when plugged in). A sleeping PC is offline; the dashboard
  and mobile apps will report it as unreachable.
- Optional: enable *Wake on LAN* in BIOS — note waking over the internet requires another always-on device on your LAN.
- `scripts\register-service.ps1 -AtStartup` (elevated PowerShell) starts the agent at boot without signing in.

## Claude.ai web connectors

Claude.ai's "custom connectors" are called from Anthropic's cloud and therefore need a **public** URL. Personal
Cloud OS deliberately stays private, so use Claude Desktop or Claude Code (they run on your device and reach the PC
over Tailscale). Exposing the MCP endpoint publicly (e.g. `tailscale funnel`) is not supported by this project
because it would also need an OAuth server; see [AI-INTEGRATION.md](AI-INTEGRATION.md).
