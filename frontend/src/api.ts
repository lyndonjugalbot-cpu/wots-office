import type { Lead, LeadDetail, OfficeAgent, Overview, WotsEvent } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  overview: () => request<Overview>("/api/overview"),
  office: () => request<{ agents: OfficeAgent[] }>("/api/office"),
  events: (after: number) => request<WotsEvent[]>(`/api/events?after=${after}&limit=200`),
  leads: (status?: string) => request<Lead[]>(`/api/leads${status ? `?status=${status}` : ""}`),
  lead: (id: number) => request<LeadDetail>(`/api/leads/${id}`),
  approve: (id: number, notes = "") => post<Lead>(`/api/leads/${id}/approve`, { notes }),
  reject: (id: number, notes: string) => post<Lead>(`/api/leads/${id}/reject`, { notes }),
  disqualify: (id: number, reason: string) => post<Lead>(`/api/leads/${id}/disqualify`, { reason }),
  resolve: (id: number, to_status: string, notes = "") => post<Lead>(`/api/leads/${id}/resolve`, { to_status, notes }),
  tick: () => post<{ ran: boolean; summary: string }>("/api/tick"),
  importSamples: () => post<{ created: number; skipped: string[] }>("/api/import-samples"),
  importCsv: (file: File, scope: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("scope", scope);
    return request<{ created: number; skipped: string[] }>("/api/import", { method: "POST", body: form });
  },
  fileUrl: (leadId: number, path: string) =>
    `/api/leads/${leadId}/files/${path.split("/").map(encodeURIComponent).join("/")}`,
};
