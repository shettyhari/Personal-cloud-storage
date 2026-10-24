// Removes a server: deletes its row and its agent account (so its saved credentials stop working).
// Called from the web dashboard by the owner with a 2FA (aal2) session.
import { createClient } from "npm:@supabase/supabase-js@2";

const url = Deno.env.get("SUPABASE_URL")!;
const admin = createClient(url, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false, autoRefreshToken: false },
});
const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization, apikey, content-type, x-client-info",
  "access-control-allow-methods": "POST, OPTIONS",
};
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "content-type": "application/json" } });

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: cors });
  if (req.method !== "POST") return json(405, { error: "POST only" });
  const jwt = (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "");
  const { data: userData, error } = await admin.auth.getUser(jwt);
  if (error || !userData.user) return json(401, { error: "sign in required" });
  const claims = JSON.parse(atob(jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  if (claims.aal !== "aal2") return json(403, { error: "two-factor authentication required" });

  const { server_id } = await req.json().catch(() => ({ server_id: null }));
  const { data: server } = await admin.from("servers").select("id, owner_id, agent_user_id")
    .eq("id", String(server_id)).maybeSingle();
  if (!server || server.owner_id !== userData.user.id) return json(404, { error: "server not found" });

  await admin.from("servers").delete().eq("id", server.id);
  if (server.agent_user_id) await admin.auth.admin.deleteUser(server.agent_user_id);
  return json(200, { ok: true });
});
