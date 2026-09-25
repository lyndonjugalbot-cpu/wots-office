import type { Lead, LeadDetail, Me, OfficeAgent, Overview, Team, TradeList, WotsEvent } from "./types";

/** Thrown on 401: the browser has no valid session cookie. */
export class SignedOut extends Error {}

let currentOffice: string | null = null;

/** Which office every request acts for (sent as X-Org; the API defaults to the first membership). */
export function setOffice(slug: string | null) {
  currentOffice = slug;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (currentOffice) headers.set("X-Org", currentOffice);
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep the status text */
    }
    if (res.status === 401) throw new SignedOut(detail);
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

const item = (id: string) => `/api/items/${encodeURIComponent(id)}`;

export const api = {
  me: () => request<Me>("/api/me"),
  overview: () => request<Overview>("/api/overview"),
  office: () => request<{ agents: OfficeAgent[] }>("/api/office"),
  events: (after: number) => request<WotsEvent[]>(`/api/events?after=${after}&limit=200`),
  leads: (status?: string) => request<Lead[]>(`/api/items${status ? `?status=${status}` : ""}`),
  lead: (id: string) => request<LeadDetail>(item(id)),
  approve: (id: string, notes = "") => post<Lead>(`${item(id)}/approve`, { notes }),
  reject: (id: string, notes: string) => post<Lead>(`${item(id)}/reject`, { notes }),
  disqualify: (id: string, reason: string) => post<Lead>(`${item(id)}/disqualify`, { reason }),
  resolve: (id: string, to_status: string, notes = "") => post<Lead>(`${item(id)}/resolve`, { to_status, notes }),
  tick: () => post<{ ran: boolean; summary: string }>("/api/tick"),
  importSamples: () => post<{ created: number; skipped: string[] }>("/api/import-samples"),
  importCsv: (file: File, workflow: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("workflow", workflow);
    return request<{ created: number; skipped: string[] }>("/api/import", { method: "POST", body: form });
  },
  trades: () => request<TradeList>("/api/trades"),
  research: (country: string, trade: string, regions: string[], limit: number, source: string) =>
    post<{ created: number; requests: number; summary: string }>("/api/research", { country, trade, regions, limit, source }),
  team: () => request<Team>("/api/team"),
  hire: (type: string, name: string, config: Record<string, unknown>) =>
    post<{ id: string; name: string }>("/api/team/hire", { type, name, config }),
  fire: (id: string) => post<{ ok: boolean }>(`/api/team/${encodeURIComponent(id)}/fire`),
  logout: () => fetch("/auth/logout", { method: "POST", credentials: "same-origin" }),
  /** A file in an item's folder, via the signed `files_base` from its detail (works in sandboxed previews). */
  fileUrl: (filesBase: string, path: string) => filesBase + path.split("/").map(encodeURIComponent).join("/"),
};
