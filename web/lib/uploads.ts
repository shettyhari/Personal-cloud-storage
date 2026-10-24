// Chunked, resumable uploads with automatic retry. State is a tiny external store used via useSyncExternalStore.
import { api, ApiError } from "./api";
import { CLOUD } from "./cloud";
import { cloudUpload } from "./cloud-transfer";

export type UploadItem = {
  key: string; name: string; size: number; sent: number; status: "queued" | "uploading" | "verifying" | "done" | "error" | "cancelled";
  error?: string; rootId: number; dir: string; startedAt?: number; speed?: number;
};

type Task = { item: UploadItem; file: File; overwrite: boolean; cancel: boolean };

const tasks: Task[] = [];
const listeners = new Set<() => void>();
let snapshot: UploadItem[] = [];
let active = 0;
const MAX_PARALLEL = 2;
const onDone = new Set<(item: UploadItem) => void>();

function emit() {
  snapshot = tasks.map((t) => ({ ...t.item }));
  listeners.forEach((l) => l());
}

export const uploadStore = {
  subscribe(l: () => void) { listeners.add(l); return () => listeners.delete(l); },
  get: () => snapshot,
  onComplete(fn: (item: UploadItem) => void) { onDone.add(fn); return () => onDone.delete(fn); },
  clearFinished() {
    for (let i = tasks.length - 1; i >= 0; i--) if (["done", "cancelled"].includes(tasks[i].item.status)) tasks.splice(i, 1);
    emit();
  },
  cancel(key: string) {
    const t = tasks.find((x) => x.item.key === key);
    if (t) { t.cancel = true; if (t.item.status === "queued") { t.item.status = "cancelled"; emit(); } }
  },
  retry(key: string) {
    const t = tasks.find((x) => x.item.key === key);
    if (t && t.item.status === "error") { t.item.status = "queued"; t.item.error = undefined; t.cancel = false; emit(); pump(); }
  },
};

export function enqueueUploads(files: File[], rootId: number, dir: string, overwrite = false) {
  for (const file of files) {
    // Files from folder drag-and-drop carry a relative path - keep the structure.
    const rel = (file as File & { relativePath?: string; webkitRelativePath?: string }).relativePath
      || (file as File & { webkitRelativePath?: string }).webkitRelativePath || "";
    const sub = rel.includes("/") ? rel.split("/").slice(0, -1).join("/") : "";
    const targetDir = [dir, sub].filter(Boolean).join("/");
    tasks.push({
      file, overwrite, cancel: false,
      item: { key: `${Date.now()}-${Math.random()}`, name: file.name, size: file.size, sent: 0, status: "queued", rootId, dir: targetDir },
    });
  }
  emit();
  pump();
}

function pump() {
  while (active < MAX_PARALLEL) {
    const next = tasks.find((t) => t.item.status === "queued" && !t.cancel);
    if (!next) return;
    active++;
    next.item.status = "uploading";
    emit();
    run(next).finally(() => { active--; emit(); pump(); });
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function run(t: Task) {
  const { file, item } = t;
  item.startedAt = Date.now();
  if (CLOUD) return runCloud(t);
  let attempt = 0;
  while (true) {
    try {
      const init = await api<{ id: string; received: number; chunk_size: number }>("/api/uploads", {
        method: "POST",
        json: { root_id: item.rootId, dir: item.dir, filename: file.name, size: file.size, overwrite: t.overwrite },
      });
      let offset = init.received;
      item.sent = offset;
      emit();
      while (offset < file.size) {
        if (t.cancel) {
          await api(`/api/uploads/${init.id}`, { method: "DELETE" }).catch(() => undefined);
          item.status = "cancelled";
          return;
        }
        const chunk = file.slice(offset, Math.min(offset + init.chunk_size, file.size));
        const t0 = performance.now();
        const res = await api<{ received: number }>(`/api/uploads/${init.id}?offset=${offset}`, { method: "PUT", body: chunk });
        offset = res.received;
        item.sent = offset;
        item.speed = chunk.size / Math.max(0.001, (performance.now() - t0) / 1000);
        attempt = 0;
        emit();
      }
      item.status = "verifying";
      emit();
      await api(`/api/uploads/${init.id}/complete`, { method: "POST", json: {} });
      item.status = "done";
      item.sent = file.size;
      onDone.forEach((fn) => fn(item));
      return;
    } catch (e) {
      const err = e as ApiError;
      // Offset mismatch / network drop: the next loop asks the server where to resume.
      const retryable = err.status === 0 || err.status === 409 || err.status >= 500 || err.status === 429;
      if (retryable && attempt < 8 && !t.cancel) {
        attempt++;
        item.error = `Connection problem - retrying (${attempt})`;
        emit();
        await sleep(Math.min(30000, 1000 * 2 ** attempt));
        item.error = undefined;
        continue;
      }
      item.status = "error";
      item.error = err.message || "Upload failed";
      return;
    }
  }
}

async function runCloud(t: Task) {
  const { file, item } = t;
  let attempt = 0;
  while (true) {
    try {
      const t0 = performance.now();
      await cloudUpload(file, item.rootId, item.dir, t.overwrite, (sent) => {
        item.sent = sent;
        item.speed = sent / Math.max(0.001, (performance.now() - t0) / 1000);
        if (sent >= file.size) item.status = "verifying";
        emit();
      }, () => t.cancel);
      item.status = "done";
      item.sent = file.size;
      onDone.forEach((fn) => fn(item));
      return;
    } catch (e) {
      if (t.cancel) { item.status = "cancelled"; return; }
      const err = e as ApiError;
      if (attempt < 5 && (err.status === undefined || err.status === 0 || err.status >= 500)) {
        attempt++;
        item.error = `Connection problem - retrying (${attempt})`;
        emit();
        await sleep(Math.min(30000, 2000 * 2 ** attempt));
        item.error = undefined;
        continue;
      }
      item.status = "error";
      item.error = (e as Error).message || "Upload failed";
      return;
    }
  }
}
