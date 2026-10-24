"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useSyncExternalStore } from "react";
import { CLOUD, closeChannels, getServerId, isOnline, onServerChange, sb, setServerId, type Server } from "./cloud";

export type CloudSession = { email: string; userId: string; aal: "aal1" | "aal2" | null; hasFactor: boolean } | null;

export function useCloudSession() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["cloud-session"],
    enabled: CLOUD,
    staleTime: 60_000,
    queryFn: async (): Promise<CloudSession> => {
      const { data } = await sb().auth.getSession();
      if (!data.session) return null;
      const [{ data: aal }, { data: factors }] = await Promise.all([
        sb().auth.mfa.getAuthenticatorAssuranceLevel(), sb().auth.mfa.listFactors(),
      ]);
      return { email: data.session.user.email || "", userId: data.session.user.id,
               aal: (aal?.currentLevel as "aal1" | "aal2") ?? null, hasFactor: (factors?.totp || []).some((f) => f.status === "verified") };
    },
  });
  useEffect(() => {
    if (!CLOUD) return;
    const { data } = sb().auth.onAuthStateChange((event) => {
      if (event === "SIGNED_OUT") closeChannels();
      if (event !== "TOKEN_REFRESHED") qc.invalidateQueries({ queryKey: ["cloud-session"] });
    });
    return () => data.subscription.unsubscribe();
  }, [qc]);
  return q;
}

export function useServers(enabled = true) {
  return useQuery({
    queryKey: ["servers"],
    enabled: CLOUD && enabled,
    refetchInterval: 10_000,
    queryFn: async () => {
      const { data, error } = await sb().from("servers").select("*").order("created_at");
      if (error) throw error;
      return (data || []) as Server[];
    },
  });
}

export function useSelectedServerId() {
  return useSyncExternalStore(onServerChange, getServerId, () => null);
}

/** Selected server row; auto-selects the first server when none is chosen. */
export function useSelectedServer(enabled = true) {
  const servers = useServers(enabled);
  const id = useSelectedServerId();
  const qc = useQueryClient();
  const list = servers.data || [];
  const current = list.find((s) => s.id === id) || null;
  useEffect(() => {
    if (!servers.data) return;
    if ((!id || !current) && list.length) setServerId((list.find(isOnline) || list[0]).id);
    if (id && !current && !list.length) setServerId(null);
  }, [servers.data, id, current, list]);
  useEffect(() => onServerChange(() => {
    closeChannels();
    qc.removeQueries({ predicate: (q) => !["servers", "cloud-session"].includes(String(q.queryKey[0])) });
  }), [qc]);
  return { servers, current, online: isOnline(current) };
}

export async function cloudSignOut() {
  closeChannels();
  setServerId(null);
  await sb().auth.signOut();
  location.href = "/login";
}
