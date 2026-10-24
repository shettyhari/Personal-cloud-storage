"use client";
// File transfers in the hosted dashboard: data is staged through the private `relay` bucket in parts
// (see /cloud/transfer/* in server/pcloud/cloudlink/tunnel.py).
import { serverApi, sb } from "./cloud";

type Staged = { transfer_id: string; name: string; size: number; mime: string; parts: { url: string; size: number }[] };

async function fetchParts(t: Staged, onProgress?: (done: number, total: number) => void): Promise<Blob> {
  const chunks: Blob[] = [];
  let done = 0;
  for (const p of t.parts) {
    const r = await fetch(p.url);
    if (!r.ok) throw new Error(`Download failed (${r.status})`);
    const b = await r.blob();
    chunks.push(b);
    done += b.size;
    onProgress?.(done, t.size);
  }
  return new Blob(chunks, { type: t.mime });
}

function save(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function cloudFileBlob(rootId: number, path: string, onProgress?: (d: number, t: number) => void) {
  const t = await serverApi<Staged>("POST", "/cloud/transfer/download", { root_id: rootId, path }, 300_000);
  return { blob: await fetchParts(t, onProgress), name: t.name, mime: t.mime };
}

export async function cloudDownload(rootId: number, path: string) {
  const { blob, name } = await cloudFileBlob(rootId, path);
  save(blob, name);
}

export async function cloudZip(items: { root_id: number; path: string }[], name: string) {
  const t = await serverApi<Staged>("POST", "/cloud/transfer/zip", { items, name }, 600_000);
  save(await fetchParts(t), t.name);
}

type Slot = { transfer_id: string; prefix: string; part_size: number; parts: number };

/** Upload a file to the selected server through the relay; resumes parts already in the relay. */
export async function cloudUpload(file: File, rootId: number, dir: string, overwrite: boolean,
                                  onProgress: (sent: number) => void, cancelled: () => boolean) {
  const slot = await serverApi<Slot>("POST", "/cloud/transfer/begin",
    { root_id: rootId, dir, filename: file.name, size: file.size, overwrite }, 60_000);
  const bucket = sb().storage.from("relay");
  const { data: existing } = await bucket.list(slot.prefix);
  const have = new Set((existing || []).map((o) => o.name));
  let sent = 0;
  for (let i = 0; i < slot.parts; i++) {
    if (cancelled()) throw new Error("cancelled");
    const name = `part-${String(i).padStart(5, "0")}`;
    const piece = file.slice(i * slot.part_size, Math.min((i + 1) * slot.part_size, file.size));
    if (!have.has(name)) {
      const { error } = await bucket.upload(`${slot.prefix}/${name}`, piece, { upsert: true, contentType: "application/octet-stream" });
      if (error) throw new Error(error.message);
    }
    sent += piece.size;
    onProgress(sent);
  }
  return serverApi("POST", "/cloud/transfer/commit", { transfer_id: slot.transfer_id }, 900_000);
}
