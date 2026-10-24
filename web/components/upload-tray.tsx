"use client";
import { CheckCircle2, ChevronDown, ChevronUp, RotateCw, UploadCloud, X, XCircle } from "lucide-react";
import { useState, useSyncExternalStore } from "react";
import { bytes, rate } from "@/lib/format";
import { uploadStore } from "@/lib/uploads";
import { Button, Progress } from "./ui";

export function UploadTray() {
  const items = useSyncExternalStore(uploadStore.subscribe, uploadStore.get, uploadStore.get);
  const [collapsed, setCollapsed] = useState(false);
  if (!items.length) return null;
  const activeCount = items.filter((i) => ["queued", "uploading", "verifying"].includes(i.status)).length;
  const total = items.reduce((a, i) => a + i.size, 0);
  const sent = items.reduce((a, i) => a + i.sent, 0);
  return (
    <div className="fixed bottom-4 left-4 z-50 w-[min(380px,calc(100vw-2rem))] rounded-xl border bg-card shadow-xl lg:left-auto lg:right-4">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <UploadCloud className="h-4 w-4 text-accent" />
        <div className="flex-1 text-sm font-medium">
          {activeCount ? `Uploading ${activeCount} file${activeCount > 1 ? "s" : ""}` : "Uploads finished"}
          <span className="ml-2 text-xs font-normal text-muted-foreground">{bytes(sent)} / {bytes(total)}</span>
        </div>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setCollapsed(!collapsed)} aria-label="Toggle uploads">
          {collapsed ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </Button>
        {!activeCount && <Button variant="ghost" size="icon" className="h-7 w-7" onClick={uploadStore.clearFinished} aria-label="Clear"><X className="h-4 w-4" /></Button>}
      </div>
      {!collapsed && (
        <ul className="max-h-72 space-y-2 overflow-y-auto p-3">
          {items.map((i) => (
            <li key={i.key} className="text-sm">
              <div className="flex items-center gap-2">
                {i.status === "done" ? <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
                  : i.status === "error" ? <XCircle className="h-4 w-4 shrink-0 text-danger" /> : null}
                <span className="min-w-0 flex-1 truncate" title={i.name}>{i.name}</span>
                <span className="shrink-0 text-xs text-muted-foreground tabular">
                  {i.status === "uploading" ? `${Math.round((i.sent / Math.max(1, i.size)) * 100)}% · ${rate(i.speed)}`
                    : i.status === "verifying" ? "Verifying…" : i.status === "queued" ? "Waiting" : i.status === "cancelled" ? "Cancelled" : bytes(i.size)}
                </span>
                {i.status === "error" && <button className="text-muted-foreground hover:text-foreground" onClick={() => uploadStore.retry(i.key)} aria-label="Retry"><RotateCw className="h-3.5 w-3.5" /></button>}
                {["queued", "uploading"].includes(i.status) && <button className="text-muted-foreground hover:text-foreground" onClick={() => uploadStore.cancel(i.key)} aria-label="Cancel"><X className="h-3.5 w-3.5" /></button>}
              </div>
              {["uploading", "verifying", "queued"].includes(i.status) && <Progress className="mt-1 h-1" value={(i.sent / Math.max(1, i.size)) * 100} />}
              {i.error && <div className="mt-0.5 text-xs text-danger">{i.error}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
