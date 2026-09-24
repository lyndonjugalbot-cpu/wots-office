export type AgentStatus = "idle" | "working" | "waiting" | "done" | "error";

/** A desk in the office, as returned by /api/office. */
export interface OfficeAgent {
  id: string;
  name: string;
  title: string;
  color: string;
  kind: "ceo" | "atlas" | "worker";
  status: AgentStatus;
  bubble: string;
  style: string | null;
}

export interface Overview {
  dry_run: boolean;
  auto_send: boolean;
  wip_mode: string;
  spend_today: number;
  spend_cap: number;
  counts: Record<string, Record<string, number>>;
  designers: { name: string; active: number; max: number; style: string | null }[];
  pending: { approvals: number; escalations: number };
  tick_seconds: number;
}

export interface WotsEvent {
  id: number;
  lead_id: number | null;
  business: string | null;
  from: string | null;
  to: string | null;
  actor: string;
  note: string | null;
  ts: string;
}

export interface Lead {
  id: number;
  scope: string;
  status: string;
  business_name: string;
  category: string | null;
  description: string | null;
  country: string;
  region: string | null;
  timezone: string | null;
  address: string | null;
  phone: string | null;
  email: string | null;
  contact_name: string | null;
  assigned_to: string | null;
  fix_count: number;
  disqualify_reason: string | null;
  source: string | null;
  updated_at: string;
}

export interface QAIssue {
  severity: "high" | "medium" | "low";
  description: string;
  where: string;
  suggested_fix: string;
}

export interface QAReportFile {
  passed: boolean;
  issues: QAIssue[];
  lighthouse: { scores?: Record<string, number>; failing_audits?: Record<string, string[]> };
  screenshots: string[];
}

export interface LeadDetail {
  lead: Lead;
  events: { id: number; from: string | null; to: string | null; actor: string; note: string | null; ts: string }[];
  artifacts: { kind: string; path: string; version: number; by: string }[];
  approvals: { kind: string; decision: string; notes: string | null; at: string }[];
  qa: QAReportFile | null;
  qa_runs: number;
  copy: { assumptions?: string[]; headline?: string } | null;
  has_site: boolean;
  resume_status: string | null;
}
