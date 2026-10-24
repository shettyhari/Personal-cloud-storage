"use client";
// Minimal dependency-free charts (inline SVG).
import { cn } from "@/lib/format";

export function Sparkline({ values, max, className, tone = "accent" }: { values: number[]; max?: number; className?: string; tone?: "accent" | "success" | "warning" }) {
  const w = 300, h = 60;
  if (values.length < 2) return <div className={cn("h-[60px]", className)} />;
  const m = max ?? Math.max(1, ...values);
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - (Math.min(v, m) / m) * (h - 4) - 2]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const color = tone === "success" ? "var(--success)" : tone === "warning" ? "var(--warning)" : "var(--accent)";
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={cn("h-[60px] w-full", className)} aria-hidden>
      <path d={`${d} L${w},${h} L0,${h} Z`} fill={color} opacity={0.1} />
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Bars({ items, format }: { items: { label: string; value: number }[]; format: (n: number) => string }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="space-y-2">
      {items.map((i) => (
        <li key={i.label} className="text-sm">
          <div className="mb-1 flex justify-between gap-2"><span className="truncate">{i.label}</span><span className="shrink-0 text-muted-foreground tabular">{format(i.value)}</span></div>
          <div className="h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-accent" style={{ width: `${(i.value / max) * 100}%` }} /></div>
        </li>
      ))}
    </ul>
  );
}
