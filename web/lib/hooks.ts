"use client";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { api, setCsrf, type Me } from "./api";
import { CLOUD } from "./cloud";
import { useSelectedServer, useSelectedServerId } from "./cloud-hooks";

export function useMe() {
  const sid = useSelectedServerId();
  return useQuery({
    queryKey: ["me", CLOUD ? sid : "local"],
    enabled: !CLOUD || !!sid,
    queryFn: async () => {
      const me = await api<Me>("/api/auth/me");
      setCsrf(me.csrf);
      return me;
    },
    staleTime: 30_000,
  });
}

/** Polls the unauthenticated health endpoint so the UI can show when the home PC is unreachable. */
export function useServerOnline() {
  const q = useQuery({
    queryKey: ["health"],
    enabled: !CLOUD,
    queryFn: async () => {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (!r.ok) throw new Error("offline");
      return r.json() as Promise<{ status: string; version: string }>;
    },
    refetchInterval: 15_000,
    retry: 1,
  });
  const cloud = useSelectedServer(CLOUD);
  if (CLOUD) return { online: cloud.online, checking: cloud.servers.isLoading, version: cloud.current?.version ?? undefined };
  return { online: q.isSuccess && !q.isError, checking: q.isLoading, version: q.data?.version };
}

export function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  useEffect(() => {
    setTheme(document.documentElement.classList.contains("dark") ? "dark" : "light");
  }, []);
  const toggle = useCallback(() => {
    const next = document.documentElement.classList.toggle("dark") ? "dark" : "light";
    try { localStorage.setItem("pcloud-theme", next); } catch { /* storage unavailable */ }
    setTheme(next);
  }, []);
  return { theme, toggle };
}

export function useHotkey(key: string, fn: (e: KeyboardEvent) => void, opts: { ctrl?: boolean } = {}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName) || target?.isContentEditable;
      if (opts.ctrl ? (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === key : !typing && !e.ctrlKey && !e.metaKey && e.key === key) {
        fn(e);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [key, fn, opts.ctrl]);
}
