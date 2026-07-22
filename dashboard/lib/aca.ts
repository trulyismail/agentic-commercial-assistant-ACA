/**
 * Client serveur pour aca/api.py (§12 item 8). Toujours appelé depuis des Server Components / Server
 * Actions / Route Handlers — ACA_API_KEY n'atteint donc jamais le navigateur, seul le cookie de
 * session (lib/session.ts) y transite.
 */
import "server-only";
import type { HistoryRow, PendingThread, SettingsResponse, Stats, ThreadSnapshot } from "./types";

const BASE_URL = process.env.ACA_API_URL ?? "http://localhost:8000";

async function acaFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const apiKey = process.env.ACA_API_KEY;
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(apiKey ? { "X-API-Key": apiKey } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`ACA API ${path} -> ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const getPendingThreads = () => acaFetch<PendingThread[]>("/threads/pending");
export const getThreadHistory = (limit = 100) => acaFetch<HistoryRow[]>(`/threads/history?limit=${limit}`);
export const getThread = (threadId: string) => acaFetch<ThreadSnapshot>(`/threads/${threadId}`);
export const getStats = (days = 30) => acaFetch<Stats>(`/stats?days=${days}`);
export const getSettings = () => acaFetch<SettingsResponse>("/settings");

export const postSettings = (values: Record<string, string>) =>
  acaFetch<SettingsResponse>("/settings", { method: "POST", body: JSON.stringify({ values }) });

export const validateThread = (threadId: string, payload: { validated_by?: string; edited_draft?: string }) =>
  acaFetch<ThreadSnapshot>(`/threads/${threadId}/valider`, { method: "POST", body: JSON.stringify(payload) });

export const rejectThread = (threadId: string) =>
  acaFetch<ThreadSnapshot>(`/threads/${threadId}/rejeter`, { method: "POST" });

export const clarifyThread = (threadId: string, answer: string) =>
  acaFetch<ThreadSnapshot>(`/threads/${threadId}/clarifier`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
