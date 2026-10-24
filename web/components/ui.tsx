"use client";
// Small shadcn/ui-style component set (Radix primitives + Tailwind).
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { AlertCircle, CheckCircle2, Info, Loader2, X } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/format";

// ------------------------------------------------------------------ Button
type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "outline" | "ghost" | "danger" | "secondary" | "accent";
  size?: "sm" | "md" | "icon" | "lg";
  loading?: boolean;
};
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "md", loading, children, disabled, ...p }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors",
        "disabled:pointer-events-none disabled:opacity-50 cursor-pointer",
        variant === "default" && "bg-primary text-primary-foreground hover:opacity-90",
        variant === "accent" && "bg-accent text-white hover:opacity-90",
        variant === "secondary" && "bg-muted text-foreground hover:bg-border/70",
        variant === "outline" && "border bg-card hover:bg-muted",
        variant === "ghost" && "hover:bg-muted",
        variant === "danger" && "bg-danger text-white hover:opacity-90",
        size === "sm" && "h-8 px-3 text-xs",
        size === "md" && "h-9 px-4",
        size === "lg" && "h-11 px-6 text-base",
        size === "icon" && "h-9 w-9",
        className,
      )}
      {...p}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  ),
);
Button.displayName = "Button";

// -------------------------------------------------------------------- Card
export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-xl border bg-card", className)} {...p} />;
}
export function CardHeader({ title, description, action, className }: {
  title: React.ReactNode; description?: React.ReactNode; action?: React.ReactNode; className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4 px-5 pt-5", className)}>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold">{title}</h3>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}
export function CardBody({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...p} />;
}

// ------------------------------------------------------------------- Inputs
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...p }, ref) => (
    <input ref={ref} className={cn(
      "h-9 w-full rounded-md border border-input bg-card px-3 text-sm placeholder:text-muted-foreground",
      "focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50", className)} {...p} />
  ),
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...p }, ref) => (
    <textarea ref={ref} className={cn(
      "w-full rounded-md border border-input bg-card px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring",
      className)} {...p} />
  ),
);
Textarea.displayName = "Textarea";

export function Select({ className, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn("h-9 rounded-md border border-input bg-card px-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring", className)} {...p} />;
}

export function Label({ className, ...p }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-sm font-medium", className)} {...p} />;
}

export function Field({ label, hint, children, className }: { label: string; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function Switch({ checked, onCheckedChange, disabled, label, description }: {
  checked: boolean; onCheckedChange: (v: boolean) => void; disabled?: boolean; label?: string; description?: React.ReactNode;
}) {
  const id = React.useId();
  const sw = (
    <SwitchPrimitive.Root id={id} checked={checked} onCheckedChange={onCheckedChange} disabled={disabled}
      className="peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent bg-input transition-colors data-[state=checked]:bg-accent disabled:opacity-50">
      <SwitchPrimitive.Thumb className="block h-4 w-4 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-4" />
    </SwitchPrimitive.Root>
  );
  if (!label) return sw;
  return (
    <div className="flex items-start justify-between gap-4 py-2">
      <label htmlFor={id} className="min-w-0 cursor-pointer">
        <div className="text-sm font-medium">{label}</div>
        {description && <div className="text-xs text-muted-foreground">{description}</div>}
      </label>
      {sw}
    </div>
  );
}

// ------------------------------------------------------------------- Badge
export function Badge({ tone = "neutral", className, ...p }: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "success" | "warning" | "danger" | "accent";
}) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
      tone === "neutral" && "bg-muted text-muted-foreground",
      tone === "success" && "border-success/30 bg-success/10 text-success",
      tone === "warning" && "border-warning/30 bg-warning/10 text-warning",
      tone === "danger" && "border-danger/30 bg-danger/10 text-danger",
      tone === "accent" && "border-accent/30 bg-accent-soft text-accent", className)} {...p} />
  );
}

export function StatusDot({ ok, className }: { ok: boolean | null; className?: string }) {
  return <span className={cn("inline-block h-2 w-2 rounded-full", ok === null ? "bg-muted-foreground" : ok ? "bg-success" : "bg-danger", className)} />;
}

export function Progress({ value, className, tone = "accent" }: { value: number; className?: string; tone?: "accent" | "warning" | "danger" | "success" }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)}>
      <div className={cn("h-full rounded-full transition-[width]",
        tone === "accent" && "bg-accent", tone === "warning" && "bg-warning", tone === "danger" && "bg-danger",
        tone === "success" && "bg-success")} style={{ width: `${v}%` }} />
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin text-muted-foreground", className)} />;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground"><Spinner />{label}</div>;
}

export function Empty({ icon, title, description, action }: { icon?: React.ReactNode; title: string; description?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && <div className="mb-1 text-muted-foreground">{icon}</div>}
      <div className="text-sm font-medium">{title}</div>
      {description && <div className="max-w-md text-sm text-muted-foreground">{description}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorBox({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className={cn("flex items-start gap-2 rounded-md border border-danger/30 bg-danger/5 p-3 text-sm text-danger", className)}>
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /> <span>{msg}</span>
    </div>
  );
}

export function Notice({ children, tone = "info", className }: { children: React.ReactNode; tone?: "info" | "warning" | "success"; className?: string }) {
  const Icon = tone === "success" ? CheckCircle2 : tone === "warning" ? AlertCircle : Info;
  return (
    <div className={cn("flex items-start gap-2 rounded-md border p-3 text-sm",
      tone === "info" && "border-accent/25 bg-accent-soft text-foreground",
      tone === "warning" && "border-warning/30 bg-warning/10",
      tone === "success" && "border-success/30 bg-success/10", className)}>
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", tone === "info" && "text-accent", tone === "warning" && "text-warning", tone === "success" && "text-success")} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function Code({ children, className }: { children: React.ReactNode; className?: string }) {
  return <code className={cn("rounded bg-muted px-1.5 py-0.5 font-mono text-[0.8em]", className)}>{children}</code>;
}

export function CopyBox({ value, multiline, className }: { value: string; multiline?: boolean; className?: string }) {
  const [copied, setCopied] = React.useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); } catch {
      const t = document.createElement("textarea"); t.value = value; document.body.appendChild(t); t.select();
      document.execCommand("copy"); t.remove();
    }
    setCopied(true); setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className={cn("flex items-start gap-2 rounded-md border bg-muted/60 p-2", className)}>
      {multiline
        ? <pre className="min-w-0 flex-1 overflow-x-auto whitespace-pre font-mono text-xs leading-relaxed">{value}</pre>
        : <code className="min-w-0 flex-1 truncate py-1 font-mono text-xs">{value}</code>}
      <Button size="sm" variant="outline" onClick={copy}>{copied ? "Copied" : "Copy"}</Button>
    </div>
  );
}

// ------------------------------------------------------------------ Dialog
export function Dialog({ open, onOpenChange, title, description, children, footer, wide }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: React.ReactNode; description?: React.ReactNode;
  children?: React.ReactNode; footer?: React.ReactNode; wide?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <DialogPrimitive.Content className={cn(
          "fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border bg-card p-5 shadow-xl focus:outline-none",
          wide ? "max-w-4xl" : "max-w-lg")}>
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <DialogPrimitive.Title className="text-base font-semibold">{title}</DialogPrimitive.Title>
              {description ? <DialogPrimitive.Description className="mt-1 text-sm text-muted-foreground">{description}</DialogPrimitive.Description>
                : <DialogPrimitive.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</DialogPrimitive.Description>}
            </div>
            <DialogPrimitive.Close className="rounded p-1 text-muted-foreground hover:bg-muted" aria-label="Close"><X className="h-4 w-4" /></DialogPrimitive.Close>
          </div>
          {children}
          {footer && <div className="mt-5 flex flex-wrap justify-end gap-2">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

// ------------------------------------------------------------------- Menus
export const Menu = DropdownPrimitive.Root;
export const MenuTrigger = DropdownPrimitive.Trigger;
export function MenuContent({ children, align = "end" }: { children: React.ReactNode; align?: "start" | "end" | "center" }) {
  return (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content align={align} sideOffset={4}
        className="z-50 min-w-[180px] rounded-lg border bg-card p-1 shadow-lg">{children}</DropdownPrimitive.Content>
    </DropdownPrimitive.Portal>
  );
}
export function MenuItem({ children, onSelect, danger, disabled, icon }: {
  children: React.ReactNode; onSelect?: () => void; danger?: boolean; disabled?: boolean; icon?: React.ReactNode;
}) {
  return (
    <DropdownPrimitive.Item disabled={disabled} onSelect={onSelect}
      className={cn("flex cursor-pointer select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-muted data-[disabled]:opacity-50",
        danger && "text-danger")}>{icon}{children}</DropdownPrimitive.Item>
  );
}
export const MenuSeparator = () => <DropdownPrimitive.Separator className="my-1 h-px bg-border" />;

// ------------------------------------------------------------------- Toasts
type Toast = { id: number; tone: "success" | "error" | "info"; text: string };
const ToastCtx = React.createContext<(tone: Toast["tone"], text: string) => void>(() => undefined);
export const useToast = () => React.useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<Toast[]>([]);
  const push = React.useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={cn("pointer-events-auto flex items-start gap-2 rounded-lg border bg-card p-3 text-sm shadow-lg",
            t.tone === "error" && "border-danger/40")}>
            {t.tone === "success" ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-success" /> : t.tone === "error"
              ? <AlertCircle className="mt-0.5 h-4 w-4 text-danger" /> : <Info className="mt-0.5 h-4 w-4 text-accent" />}
            <span className="min-w-0 flex-1 break-words">{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

// --------------------------------------------------------- Confirm dialog
export function useConfirm() {
  const [state, setState] = React.useState<{ title: string; body?: React.ReactNode; danger?: boolean; confirm: string; resolve: (v: boolean) => void } | null>(null);
  const ask = React.useCallback((title: string, opts: { body?: React.ReactNode; danger?: boolean; confirm?: string } = {}) =>
    new Promise<boolean>((resolve) => setState({ title, body: opts.body, danger: opts.danger, confirm: opts.confirm || "Confirm", resolve })), []);
  const node = state ? (
    <Dialog open onOpenChange={(o) => { if (!o) { state.resolve(false); setState(null); } }} title={state.title}
      footer={<>
        <Button variant="outline" onClick={() => { state.resolve(false); setState(null); }}>Cancel</Button>
        <Button variant={state.danger ? "danger" : "default"} onClick={() => { state.resolve(true); setState(null); }}>{state.confirm}</Button>
      </>}>
      {state.body && <div className="text-sm text-muted-foreground">{state.body}</div>}
    </Dialog>
  ) : null;
  return [ask, node] as const;
}

// ------------------------------------------------------------- Page header
export function PageHeader({ title, description, actions }: { title: string; description?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, sub, icon }: { label: string; value: React.ReactNode; sub?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between text-xs font-medium text-muted-foreground">{label}{icon}</div>
      <div className="mt-2 text-2xl font-semibold tracking-tight tabular">{value}</div>
      {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
    </Card>
  );
}

export function Table({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("overflow-x-auto", className)}><table className="w-full text-sm">{children}</table></div>;
}
export const Th = ({ children, className }: { children?: React.ReactNode; className?: string }) =>
  <th className={cn("border-b px-4 py-2 text-left text-xs font-medium text-muted-foreground", className)}>{children}</th>;
export const Td = ({ children, className, colSpan }: { children?: React.ReactNode; className?: string; colSpan?: number }) =>
  <td colSpan={colSpan} className={cn("border-b px-4 py-2.5 align-middle", className)}>{children}</td>;
