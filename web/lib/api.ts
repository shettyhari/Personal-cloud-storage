// Typed client for the home PC agent. Same-origin: the dashboard is served by the agent itself.

let csrf: string | null = null;
export const setCsrf = (t: string | null) => { csrf = t; };

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) { super(message); }
}

function message(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object" && "message" in d) return String((d as { message: unknown }).message);
  }
  if (status === 0) return "Cannot reach your home PC. It may be off, asleep or offline.";
  return `Request failed (${status})`;
}

export async function api<T = unknown>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  if (opts.json !== undefined) headers.set("content-type", "application/json");
  const method = (opts.method || (opts.json !== undefined ? "POST" : "GET")).toUpperCase();
  if (method !== "GET" && method !== "HEAD" && csrf) headers.set("x-csrf-token", csrf);
  let res: Response;
  try {
    res = await fetch(path, {
      ...opts, method, headers, credentials: "same-origin",
      body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.body,
    });
  } catch {
    throw new ApiError(0, message(null, 0));
  }
  const type = res.headers.get("content-type") || "";
  const body = type.includes("json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/") && !path.startsWith("/api/setup")) {
      const next = encodeURIComponent(location.pathname + location.search);
      if (!location.pathname.startsWith("/login")) location.href = `/login?next=${next}`;
    }
    throw new ApiError(res.status, message(body, res.status), body);
  }
  return body as T;
}

export const post = <T = unknown>(path: string, json?: unknown) => api<T>(path, { method: "POST", json: json ?? {} });
export const patch = <T = unknown>(path: string, json: unknown) => api<T>(path, { method: "PATCH", json });
export const put = <T = unknown>(path: string, json: unknown) => api<T>(path, { method: "PUT", json });
export const del = <T = unknown>(path: string) => api<T>(path, { method: "DELETE" });

export const qs = (o: Record<string, string | number | boolean | undefined | null>) =>
  new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => [k, String(v)])).toString();

// ---------------------------------------------------------------- types
export type Root = {
  id: number; name: string; path: string; read_only: boolean; webdav: boolean; ai_read: boolean; ai_write: boolean;
  versioning: boolean; index_content: boolean; access?: "r" | "rw"; available?: boolean;
  disk?: { total: number; used: number; free: number };
};
export type FileEntry = {
  name: string; path: string; root_id: number; is_dir: boolean; size: number; modified: number; created?: number;
  kind: string; mime: string | null; ext: string; match?: string; snippet?: string | null;
};
export type Me = {
  user: { id: number; username: string; role: "admin" | "user" | "readonly"; totp_enabled: boolean };
  csrf: string; roots: Root[]; server_name: string; setup_complete: boolean;
};
export type Job = {
  id: number; type: string; title: string; status: string; progress: number; message: string | null;
  result: unknown; source: string; created_by: string; created_at: number; started_at: number | null;
  finished_at: number | null; params: Record<string, unknown>;
};
export type Token = {
  id: number; name: string; kind: string; prefix: string; read_only: number; created_at: number;
  last_used: number | null; last_ip: string | null; expires_at: number | null; revoked: number; username: string;
};
export type Activity = {
  id: number; ts: number; actor_type: string; actor: string; action: string; target: string | null;
  status: string; ip: string | null; details: Record<string, unknown> | null;
};

export const downloadUrl = (rootId: number, path: string, inline = false) =>
  `/api/files/download?${qs({ root_id: rootId, path, inline: inline ? 1 : undefined })}`;
