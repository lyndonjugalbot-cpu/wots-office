export type AgentStatus = "idle" | "working" | "waiting" | "done" | "error";

/** A desk in the office, as returned by /api/office. */
export interface OfficeAgent {
  id: string;
  name: string;
  title: string;
  color: string;
  kind: "ceo" | "atlas" | "qa" | "worker";
  status: AgentStatus;
  bubble: string;
  style: string | null;
  available: boolean; // false = hired, but their type arrives in a later phase
  type?: string;
}

export interface OfficeRef {
  slug: string;
  name: string;
  roles: string[];
  is_internal: boolean;
}

export interface Me {
  user: { id: string; email: string; name: string | null };
  offices: OfficeRef[];
}

export interface Overview {
  office: OfficeRef;
  dry_run: boolean;
  auto_send: boolean;
  spend_today: number;
  spend_cap: number;
  credits: number | null; // null = unlimited (the internal office)
  counts: Record<string, Record<string, number>>;
  workflows: { key: string; active: boolean; states: string[]; waiting_for: string[]; missing_reason: string | null }[];
  designers: { name: string; active: number; max: number; mode: string | null; style: string | null }[];
  pending: { approvals: number; escalations: number; other: number };
  tick_seconds: number;
  research: Record<"osm" | "companies_house", { requests_today: number; max_per_day: number }>;
}

export interface WotsEvent {
  id: number;
  item_id: string | null;
  business: string | null;
  from: string | null;
  to: string | null;
  actor: string; // an employee's name, "You", or a system actor
  actor_kind: "user" | "employee" | "system";
  note: string | null;
  ts: string;
}

/** A work item with its lead profile, flattened. */
export interface Lead {
  id: string;
  workflow_key: string;
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
  postcode: string | null;
  entity_type: string | null;
  registry_id: string | null;
  website_found: string | null;
  social_links: Record<string, string>;
  checks: Checks;
  updated_at: string;
}

/** What the Data Verifier found, and where (see wots/employees/impl/data_verifier.py). */
export interface Checks {
  checked_at?: string;
  dedupe?: { id: string; business_name: string; match: string }[];
  website?: { listed: string | null; domains: { domain: string; status: string; evidence: string }[] };
  registry?: { source: string; candidates: string[]; match: Record<string, string> | null };
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
  item: Lead;
  gate: string | null;
  can_decide: boolean;
  send_back_to: string | null;
  events: { id: number; from: string | null; to: string | null; actor: string; note: string | null; ts: string }[];
  artifacts: { kind: string; path: string; version: number; by: string }[];
  approvals: { kind: string; decision: string; notes: string | null; at: string }[];
  qa: QAReportFile | null;
  qa_runs: number;
  copy: { assumptions?: string[]; headline?: string } | null;
  has_site: boolean;
  resume_status: string | null;
  files_base: string;
}

export interface ConfigField {
  type: "enum" | "int" | "list" | string;
  values?: string[];
  default?: unknown;
  min?: number;
  max?: number;
}

export interface Team {
  members: { id: string; name: string; type: string; type_name: string; config: Record<string, unknown>; enabled: boolean; working: boolean }[];
  catalogue: { key: string; name: string; description: string; risk: string; status: string; config_schema: Record<string, ConfigField> }[];
  can_manage: boolean;
}

export interface TradeList {
  trades: { key: string; label: string; uk_registry: boolean }[];
  sources: { key: string; label: string; countries: string[] }[];
}
