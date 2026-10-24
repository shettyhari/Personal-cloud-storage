import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export const cn = (...v: ClassValue[]) => twMerge(clsx(v));

export function bytes(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || isNaN(n)) return "—";
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  let v = Math.abs(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${n < 0 ? "-" : ""}${v.toFixed(i === 0 ? 0 : digits)} ${u[i]}`;
}

export const rate = (bps: number | null | undefined) => `${bytes(bps ?? 0)}/s`;

export function date(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { year: d.getFullYear() === now.getFullYear() ? undefined : "numeric", month: "short", day: "numeric" })
      + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function ago(ts: number | null | undefined): string {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function duration(seconds: number): string {
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

export const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);
