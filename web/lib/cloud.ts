"use client";
// Hosted ("cloud") mode: Supabase sign-in + a request relay to the selected server over a private Realtime channel.
// The protocol mirrors server/pcloud/cloudlink/tunnel.py.
import { createClient, type RealtimeChannel, type SupabaseClient } from "@supabase/supabase-js";

export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
export const SUPABASE_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";
export const CLOUD = Boolean(SUPABASE_URL && SUPABASE_KEY);

let client: SupabaseClient | null = null;
export function sb(): SupabaseClient {
  if (!client) client = createClient(SUPABASE_URL, SUPABASE_KEY, { auth: { persistSession: true, autoRefreshToken: true } });
  return client;
}

export class CloudError extends Error {
  constructor(public status: number, message: string, public body?: unknown) { super(message); }
}

// ------------------------------------------------------------ selected server
export type Server = {
  id: string; name: string; kind: "windows_pc" | "linux_vps"; hostname: string | null; os: string | null;
  version: string | null; status: "online" | "offline"; last_seen: string | null; created_at: string;
  metrics: { cpu?: number; mem?: number; mem_total?: number; up_bps?: number; down_bps?: number; boot_time?: number;
             disks?: { mount: string; total: number; free: number }[]; hostname?: string; os?: string };
  roots: { id: number; name: string }[];
};

export const isOnline = (s: Pick<Server, "status" | "last_seen"> | null | undefined) =>
  !!s && s.status === "online" && !!s.last_seen && Date.now() - Date.parse(s.last_seen) < 95_000;

const SERVER_KEY = "pcloud-server";
const listeners = new Set<() => void>();
export function getServerId(): string | null {
  try { return localStorage.getItem(SERVER_KEY); } catch { return null; }
}
export function setServerId(id: string | null) {
  try { if (id) localStorage.setItem(SERVER_KEY, id); else localStorage.removeItem(SERVER_KEY); } catch { /* storage blocked */ }
  listeners.forEach((l) => l());
}
export const onServerChange = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l); }; };

// --------------------------------------------------------------- relay (RPC)
type Pending = { parts: string[]; got: number; total: number; resolve: (v: { status: number; body: unknown }) => void;
                 reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> };
const pending = new Map<string, Pending>();
const channels = new Map<string, { ch: RealtimeChannel; ready: Promise<void> }>();

function onResponse(p: { id: string; status: number; part: number; parts: number; chunk: string }) {
  const e = pending.get(p.id);
  if (!e) return;
  if (e.parts[p.part] === undefined) { e.parts[p.part] = p.chunk; e.got++; }
  e.total = p.parts;
  if (e.got === e.total) {
    clearTimeout(e.timer);
    pending.delete(p.id);
    let body: unknown = null;
    try { body = JSON.parse(e.parts.join("")); } catch { body = e.parts.join(""); }
    e.resolve({ status: p.status, body });
  }
}

async function channelFor(serverId: string) {
  const existing = channels.get(serverId);
  if (existing) return existing;
  const { data } = await sb().auth.getSession();
  if (!data.session) throw new CloudError(401, "Sign in required");
  await sb().realtime.setAuth(data.session.access_token);
  const ch = sb().channel(`server:${serverId}`, { config: { private: true, broadcast: { self: false, ack: false } } });
  ch.on("broadcast", { event: "res" }, ({ payload }) => onResponse(payload as never));
  const ready = new Promise<void>((resolve, reject) => {
    ch.subscribe((status, err) => {
      if (status === "SUBSCRIBED") resolve();
      else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT" || status === "CLOSED") {
        channels.delete(serverId);
        reject(new CloudError(503, status === "CHANNEL_ERROR"
          ? `Not allowed to reach this server (${err?.message || "check 2FA"})`
          : "Could not connect to the server channel"));
      }
    });
  });
  const entry = { ch, ready };
  channels.set(serverId, entry);
  return entry;
}

export function closeChannels() {
  channels.forEach(({ ch }) => { sb().removeChannel(ch); });
  channels.clear();
}

export async function rpc(serverId: string, method: string, path: string, body?: unknown, timeoutMs = 30_000):
  Promise<{ status: number; body: unknown }> {
  const { ch, ready } = await channelFor(serverId);
  await ready;
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new CloudError(504, "The server did not answer. It may be off, asleep or offline."));
    }, timeoutMs);
    pending.set(id, { parts: [], got: 0, total: -1, resolve, reject, timer });
    ch.send({ type: "broadcast", event: "req", payload: { id, method, path, body: body ?? null } })
      .then((r) => { if (r !== "ok") { clearTimeout(timer); pending.delete(id); reject(new CloudError(503, "Could not send the request")); } });
  });
}

/** Call the selected server's REST API through the relay. */
export async function serverApi<T>(method: string, path: string, body?: unknown, timeoutMs?: number): Promise<T> {
  const sid = getServerId();
  if (!sid) throw new CloudError(428, "Choose a server first (Servers page).");
  const r = await rpc(sid, method, path, body, timeoutMs);
  if (r.status >= 400) {
    const b = r.body as { detail?: unknown } | null;
    const msg = typeof b?.detail === "string" ? b.detail : `Request failed (${r.status})`;
    throw new CloudError(r.status, msg, r.body);
  }
  return r.body as T;
}

// ------------------------------------------------------ link code for agents
export function makeLinkCode(code: string): string {
  const raw = JSON.stringify({ u: SUPABASE_URL, k: SUPABASE_KEY, c: code });
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
