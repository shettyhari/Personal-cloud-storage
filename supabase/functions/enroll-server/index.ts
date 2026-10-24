// Exchanges a one-time link code for a dedicated agent account for one server.
// Called by the pcloud agent (`pcloud link <code>`); the code itself is the credential (192 random bits,
// single use, 30-minute expiry). The agent's password is returned exactly once and stored DPAPI-protected.
import { createClient } from "npm:@supabase/supabase-js@2";

const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false, autoRefreshToken: false },
});

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

async function sha256(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function randomPassword(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(36));
  return btoa(String.fromCharCode(...bytes)).replace(/[+/=]/g, "x");
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return json(405, { error: "POST only" });
  let body: Record<string, unknown>;
  try { body = await req.json(); } catch { return json(400, { error: "invalid JSON" }); }
  const code = String(body.code ?? "");
  if (!/^[0-9a-f]{48}$/.test(code)) return json(400, { error: "invalid link code" });

  const hash = await sha256(code);
  // Claim the code atomically: only an unused, unexpired code is marked used.
  const { data: claimed, error: claimErr } = await admin
    .from("enrollments")
    .update({ used_at: new Date().toISOString() })
    .eq("code_hash", hash)
    .is("used_at", null)
    .gt("expires_at", new Date().toISOString())
    .select("owner_id, name")
    .maybeSingle();
  if (claimErr) return json(500, { error: "lookup failed" });
  if (!claimed) return json(400, { error: "link code expired or already used - create a new one in Servers" });

  const serverId = crypto.randomUUID();
  const email = `server-${serverId}@agents.pcloud.invalid`;
  const password = randomPassword();
  const { data: created, error: userErr } = await admin.auth.admin.createUser({
    email, password, email_confirm: true, app_metadata: { role: "agent", server_id: serverId },
  });
  if (userErr || !created.user) return json(500, { error: "could not create server account" });

  const kind = body.kind === "linux_vps" ? "linux_vps" : "windows_pc";
  const name = String(body.name || claimed.name || body.hostname || "Server").slice(0, 60);
  const { error: insErr } = await admin.from("servers").insert({
    id: serverId, owner_id: claimed.owner_id, agent_user_id: created.user.id, name, kind,
    hostname: String(body.hostname ?? "").slice(0, 120), os: String(body.os ?? "").slice(0, 120),
    version: String(body.version ?? "").slice(0, 32),
  });
  if (insErr) {
    await admin.auth.admin.deleteUser(created.user.id);
    return json(500, { error: "could not register server" });
  }
  return json(200, { server_id: serverId, email, password });
});
