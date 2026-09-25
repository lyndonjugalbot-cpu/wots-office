# Wots Office — Build Spec v2 (multi-tenant ready)

> **For Claude Code:** This replaces `docs/SPEC.md` (v1). Save it as `docs/SPEC.md`, keep v1 as `docs/SPEC_v1.md` for reference.
> **Before changing any code:** inspect the repo, report which v1 phases/parts are already built, then follow §15 (Migration from v1) to refactor what exists. Then continue with the phase plan in §16. Do not start a phase until the previous one's acceptance criteria pass.

---

## 0. What changed from v1

| v1 | v2 |
|---|---|
| One office (ours), agents hard-coded | Many **offices** (tenants). Ours is org #1, "Wots Office" |
| Agents like Pixel/Hawk are fixed classes wired into pipelines | **Employee types** (templates) in a registry; offices **hire employees** (instances) of those types |
| States hard-coded in `states.py` per scope | **Workflows as data** (YAML). Atlas reads the workflow; no pipeline logic in code |
| `leads` table is the work item | Generic **`work_items`** table + typed detail tables (`lead_profiles`, …) |
| CEO = Lyndon | **Members** with roles (owner, ceo, manager, viewer). The CEO role holds approval gates |
| Local only | Local first, but every table has `org_id`, secrets are per org, and a SaaS layer (auth, billing, onboarding UI) comes in Phases 7–8 |

Our own agency keeps working exactly as designed in v1 — it's now just the first office running two **office templates** (Web Agency, Ad Agency).

---

## 1. Product vision

**Phase A (now): Wots Office, our agency.** AI employees find small businesses in the US/UK/AU, build websites or redesign ads on spec, and pitch them by email. The CEO approves everything before it leaves.

**Phase B (later): Wots Office as a SaaS.** Companies subscribe, name their office, choose who is CEO, pick an office template, and hire AI employees from a catalogue (graphic designer, web developer, copywriter, lead researcher, cold email specialist, QA tester, content creator, HR/team manager, …).

We build Phase A on the Phase B data model so there's no rewrite later.

---

## 2. Design principles

1. **One source of truth.** Work items live on a board with a `status`. Employees only pick up items in states they own. No file passing.
2. **Atlas (orchestrator) is deterministic Python, not an LLM.** Routing, assignment, WIP limits, retries and escalation are testable code driven by workflow YAML.
3. **Tenant isolation everywhere.** Every row has `org_id`. All DB access goes through a repository layer that requires an `OrgContext`. No raw queries without `org_id`. (Postgres row-level security added in Phase 7.)
4. **Humans gate anything external.** Nothing is deployed publicly or emailed without a CEO/manager approval. `auto_send=false` by default.
5. **Config over code.** Employee types, workflows, office templates, country rules, plans and thresholds are data.
6. **Everything is logged and metered.** Every status change → `events`. Every LLM call and billable action → `usage_events` (credits).
7. **Dry-run mode.** `DRY_RUN=true` = no sends, no deploys, capped paid API usage.

---

## 3. Core concepts

- **Organization (office):** a tenant. Has a name ("Wots Office"), plan, settings, members, employees, workflows, integrations.
- **Member:** a human user in an office. Roles:
  - `owner` — billing, integrations, can do everything
  - `ceo` — approval gates, hire/fire employees, all work
  - `manager` — approvals if the CEO delegates them, day-to-day work
  - `viewer` — read-only
  One person can be owner and CEO. An office has exactly one CEO at a time.
- **Employee type:** a template in the platform catalogue (e.g. `web_developer`). Defines instructions, tools, task kinds it handles, default model, config schema, risk level, and which plans can hire it.
- **Employee:** a hired instance of a type inside an office (e.g. "Pixel", a `web_developer` with style "clean & modern", `max_wip: 2`).
- **Workflow:** a state machine (YAML) for one kind of work — which states exist, which employee type or human gate owns each state, allowed transitions, WIP and fix-loop rules.
- **Office template:** a starter bundle = workflows + recommended employees. v2 ships `web_agency` and `ad_agency`; `content_studio` is planned.
- **Work item:** one unit of work moving through a workflow (a lead to build a site for, an advertiser to redesign ads for, a content piece…).

---

## 4. Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.x + Alembic
- **DB:** SQLite for local dev; **Postgres (Supabase)** from Phase 7. Write portable SQLAlchemy (no SQLite-only features).
- **Jobs:** Atlas tick enqueues jobs; workers execute. Phase 0–6: in-process worker pool behind a `JobQueue` interface. Phase 7: swap to a real queue (Redis + RQ/Celery, or a Postgres-backed queue) without changing agent code.
- **LLM:** Anthropic Python SDK (Messages API). Model per employee type, overridable per employee. Key in `.env` as `ANTHROPIC_API_KEY` (never committed).
- **Browser automation / QA / rendering:** Playwright (Python); Lighthouse CLI; Pillow
- **Internal dashboard (Phases 1–6):** FastAPI + Jinja2 + HTMX on localhost
- **Customer web app (Phase 8):** Next.js on Vercel, talking to the FastAPI API
- **Auth (Phase 7):** Supabase Auth (JWT verified by FastAPI)
- **Billing (Phase 8):** Stripe subscriptions + metered credits
- **Deploy previews:** Vercel CLI/API
- **Email:** `EmailProvider` interface; dev implementation writes `.eml` to `data/outbox/{org_id}/`
- **Secrets:** per-org integration secrets encrypted at rest (Fernet, key from `SECRETS_KEY` env)

---

## 5. Employee type registry

Stored as files in `wots/employees/types/{key}.yaml` + a Python class per type in `wots/employees/impl/`. Loaded into the `employee_types` table on startup (versioned).

```yaml
# wots/employees/types/web_developer.yaml
key: web_developer
display_name: Web Developer
category: design
description: Builds responsive single-page business websites from copy and brand assets.
impl: wots.employees.impl.web_developer:WebDeveloper
task_kinds: [build_website, fix_website]
default_model: strong          # alias resolved in config/models.yaml
tools: [filesystem, templates.sites]
config_schema:                  # what an office can set when hiring
  style_profile: {type: enum, values: [clean_modern, bold_warm, minimal, playful], default: clean_modern}
  max_wip: {type: int, default: 2, min: 1, max: 5}
  wip_mode: {type: enum, values: [batch, rolling], default: batch}
risk_level: low                 # low | medium | high (high = needs terms acceptance)
plans: [starter, pro, agency]
est_credits_per_task: 40
status: available               # available | beta | planned
```

### v2 catalogue

| Key | Display name | v1 name(s) | Does | Status |
|---|---|---|---|---|
| `lead_researcher` | Lead Researcher | Scout | Finds businesses from approved sources (our scraper adapter, Google Places API) | available |
| `ad_researcher` | Ad Researcher | Scout-Ads | Normalises manually captured ad-library entries into work items | available |
| `data_verifier` | Data Verifier | Ledger | Verifies, dedupes, enriches, applies country rules | available |
| `copywriter` | Copywriter | Quill | Website/ad/email copy in local spelling, no invented facts | available |
| `creative_strategist` | Creative Strategist | Lens | Writes creative briefs for ad redesigns | available |
| `graphic_designer` | Graphic Designer | Iris, Juno | Logos, hero images, ad variations (HTML/CSS → PNG) | available |
| `web_developer` | Web Developer | Pixel, Nova | Builds static sites from templates + copy + assets | available |
| `qa_tester` | QA Tester | Hawk | Tests websites and ad creatives, writes QA reports | available |
| `deployment` | Deployment Specialist | Dock | Private noindexed previews, teardown | available |
| `cold_email` | Outreach Specialist | Echo | Drafts and (after approval) sends compliant pitches | available, `risk_level: high` |
| `hr_manager` | HR / Team Manager | — (new) | Manages the **AI team**: onboarding checklist for new hires, weekly performance reports, staffing suggestions | available (Phase 6) |
| `content_creator` | Content Creator | — (new) | Social posts/captions + visuals from a brief | planned (Phase 9) |
| `office_manager` | Office Manager | Atlas | **Not hireable.** Atlas is platform infrastructure in every office | — |

Notes:
- **HR / Team Manager does not screen or hire humans.** It manages AI employees only: tracks throughput, QA pass rate, fix loops, cost per task, and recommends changes ("Web Developer queue is 9 items deep — consider hiring a second one"). This avoids employment-law/bias risk.
- **Customer-facing lead sourcing uses official APIs only** (e.g. Places API). Our private scraper adapter is enabled only for the internal Wots Office org via a feature flag (`internal_scraper_enabled`).
- Employee types with `risk_level: high` (Outreach) can only be hired after the office owner accepts the outreach terms (§11).

---

## 6. Workflows as data

Stored in `wots/workflows/{key}.yaml`. An office activates workflows; Atlas executes them. `states.py` becomes a generic loader/validator.

### State ownership kinds
- `type: <employee_type>` — any employee of that type in the office may take it (Atlas picks one with capacity)
- `assigned` — only the work item's `assigned_employee_id` may take it (used after assignment so fixes go back to the same designer)
- `assign: <employee_type>` — Atlas assigns an employee of that type (WIP rules) and moves on
- `gate: <role>` — waits for a human with that role (`ceo`, or `manager` if delegated)
- `system` — handled by Atlas itself (e.g. timeouts)
- `terminal`

`skip_if_missing: true` on a state lets a workflow run when the office hasn't hired that type (e.g. no Graphic Designer → skip logo/hero).

### Website workflow (`web_agency` template)

```yaml
key: website
work_item_kind: lead
initial: NEW
states:
  NEW:                {owner: {type: lead_researcher}, note: "created by lead researcher run"}
  VERIFY:             {owner: {type: data_verifier}}
  ENRICHED:           {owner: {type: copywriter}}
  COPY_READY:         {owner: {type: graphic_designer}, task: website_assets, skip_if_missing: true, skip_to: ASSETS_READY}
  ASSETS_READY:       {owner: {assign: web_developer}}
  ASSIGNED:           {owner: assigned}
  BUILDING:           {owner: assigned}
  IN_QA:              {owner: {type: qa_tester}}
  NEEDS_FIX:          {owner: assigned}
  READY_FOR_APPROVAL: {owner: {gate: ceo}}
  APPROVED:           {owner: {type: deployment}}
  PREVIEW_DEPLOYED:   {owner: {type: cold_email}, task: draft_pitch}
  PITCH_DRAFTED:      {owner: {gate: ceo}}
  PITCH_APPROVED:     {owner: {type: cold_email}, task: send_pitch}
  PITCHED:            {owner: system}      # follow-up timer
  REPLIED:            {owner: {gate: ceo}}
  ESCALATED:          {owner: {gate: ceo}}
  WON: terminal
  LOST: terminal
  DISQUALIFIED: terminal
transitions:
  - [NEW, VERIFY]
  - [VERIFY, ENRICHED]
  - [VERIFY, DISQUALIFIED]
  - [ENRICHED, COPY_READY]
  - [COPY_READY, ASSETS_READY]
  - [ASSETS_READY, ASSIGNED]
  - [ASSIGNED, BUILDING]
  - [BUILDING, IN_QA]
  - [IN_QA, READY_FOR_APPROVAL]
  - [IN_QA, NEEDS_FIX]
  - [NEEDS_FIX, BUILDING]
  - [NEEDS_FIX, ESCALATED]
  - [READY_FOR_APPROVAL, APPROVED]
  - [READY_FOR_APPROVAL, NEEDS_FIX]
  - [READY_FOR_APPROVAL, DISQUALIFIED]
  - [APPROVED, PREVIEW_DEPLOYED]
  - [PREVIEW_DEPLOYED, PITCH_DRAFTED]
  - [PITCH_DRAFTED, PITCH_APPROVED]
  - [PITCH_DRAFTED, PREVIEW_DEPLOYED]     # rejected → redraft
  - [PITCH_APPROVED, PITCHED]
  - [PITCHED, REPLIED]
  - [PITCHED, LOST]
  - [REPLIED, WON]
  - [REPLIED, LOST]
  - [ESCALATED, BUILDING]
  - [ESCALATED, DISQUALIFIED]
rules:
  wip:
    web_developer: {active_states: [ASSIGNED, BUILDING, IN_QA, NEEDS_FIX, READY_FOR_APPROVAL, ESCALATED]}
  fix_loop: {counter_on: NEEDS_FIX, max: 3, escalate_to: ESCALATED}
  followup: {after_days: 5, max_followups: 1, then: LOST}
```

(Note: v1's `NEW → ENRICHED` is now `NEW → VERIFY → ENRICHED` so verification is its own owned step.)

### Ad Refresh workflow (`ad_agency` template)

```yaml
key: ad_refresh
work_item_kind: lead
initial: NEW
states:
  NEW:                {owner: {type: ad_researcher}}
  VERIFY:             {owner: {type: data_verifier}}
  ENRICHED:           {owner: {type: creative_strategist}}
  BRIEFED:            {owner: {assign: graphic_designer}}
  ASSIGNED:           {owner: assigned}
  DESIGNING:          {owner: assigned}
  IN_QA:              {owner: {type: qa_tester}}
  NEEDS_FIX:          {owner: assigned}
  READY_FOR_APPROVAL: {owner: {gate: ceo}}
  APPROVED:           {owner: {type: cold_email}, task: draft_pitch}
  PITCH_DRAFTED:      {owner: {gate: ceo}}
  PITCH_APPROVED:     {owner: {type: cold_email}, task: send_pitch}
  PITCHED:            {owner: system}
  REPLIED:            {owner: {gate: ceo}}
  ESCALATED:          {owner: {gate: ceo}}
  WON: terminal
  LOST: terminal
  DISQUALIFIED: terminal
# transitions mirror the website workflow (without COPY_READY/ASSETS_READY/deployment)
rules:
  wip:
    graphic_designer: {active_states: [ASSIGNED, DESIGNING, IN_QA, NEEDS_FIX, READY_FOR_APPROVAL, ESCALATED]}
  fix_loop: {counter_on: NEEDS_FIX, max: 3, escalate_to: ESCALATED}
  followup: {after_days: 5, max_followups: 1, then: LOST}
```

### Workflow validation (on load and on office activation)
- Every state reachable from `initial`; every non-terminal state has an outgoing transition
- Every `type`/`assign` references a known employee type
- Activation fails with a clear message if the office lacks a required employee type (unless `skip_if_missing`) — e.g. "Website workflow needs a QA Tester. Hire one or pick a different template."

### Office templates

```yaml
# wots/templates/offices/web_agency.yaml
key: web_agency
display_name: Web Agency
workflows: [website]
recommended_team:
  - {type: lead_researcher, name: Scout}
  - {type: data_verifier, name: Ledger}
  - {type: copywriter, name: Quill}
  - {type: graphic_designer, name: Iris}
  - {type: web_developer, name: Pixel, config: {style_profile: clean_modern}}
  - {type: web_developer, name: Nova, config: {style_profile: bold_warm}}
  - {type: qa_tester, name: Hawk}
  - {type: deployment, name: Dock}
  - {type: cold_email, name: Echo}
```

`ad_agency` recommends Scout-Ads, Ledger, Lens, Iris, Juno, Hawk, Echo. Our internal office activates both templates (shared employees are hired once: Ledger, Iris, Hawk, Echo).

---

## 7. Atlas (orchestrator)

Runs a tick every N seconds (default 30). **Per tick, per active office** (offices processed round-robin with a per-office concurrency cap so one busy tenant can't starve others):

1. **Load** active workflows and hired, enabled employees for the office.
2. **Assign**: for items in an `assign: <type>` state, pick an employee of that type with capacity:
   - capacity = `max_wip` minus items in the workflow's `active_states` assigned to them
   - `wip_mode: batch` → only gets new work when **all** its current items reached `APPROVED` or left the pipeline (matches the CEO's "2 at a time, then 2 more" rule)
   - `wip_mode: rolling` → new work whenever below `max_wip`
   - tie-break: fewest items assigned in the last 7 days
   - sets `assigned_employee_id`, moves to next state
3. **Dispatch**: for items in employee-owned states, claim with a lease (`claimed_by`, `claim_expires_at`) and enqueue a job for the right employee. `assigned` states only go to the assigned employee.
4. **Fix routing**: entering `NEEDS_FIX` increments `fix_count` and attaches the latest QA report / CEO notes. At `max` → `escalate_to`.
5. **Retries**: failed jobs retry with backoff ×3, then → `ESCALATED` with the error.
6. **Timers**: follow-ups, preview TTL cleanup, retention purge.
7. **Budget/credits guard**: if an office hits its daily LLM budget or runs out of credits, pause its LLM employees and notify.
8. **Notify**: webhook/email when items hit a human gate or escalate.

Employees never transition items themselves. They return an `EmployeeResult` (next status, note, artifacts, usage), and Atlas validates it against the workflow and applies it.

```python
class Employee(Protocol):
    type_key: str
    def run(self, item: WorkItem, task: str, ctx: EmployeeContext) -> EmployeeResult: ...
# EmployeeContext: org, employee config, model client, repositories scoped to org,
# integration clients for this org, country rules, file store scoped to org
```

CLI:
```
wots run                         # Atlas + workers + internal dashboard
wots tick [--org wots-office]
wots import-leads leads.csv --org wots-office --workflow website
wots status [--org ...]
wots orgs create "Wots Office" --template web_agency --template ad_agency --ceo lyndon@...
wots employees hire --org ... --type web_developer --name Pixel --config style_profile=clean_modern
wots workflows validate
```

---

## 8. Employee specs (behaviour carried over from v1)

Unchanged in substance from v1; now each is an employee type implementation.

- **Lead Researcher (Scout):** adapter around our existing scraper (internal org only, feature flag) + Google Places Text Search. Filters by country/category/region. Stores `source` + `source_ref`. Respects ToS and rate limits. Claude Code: ask Lyndon for the scraper's input/output format.
- **Ad Researcher (Scout-Ads):** manual intake only (dashboard upload or `data/inbox/{org_id}/ads/` with a sidecar CSV/YAML: page name, platform, ad library URL, country, notes, checklist). **Do not build automated scraping of Meta/TikTok/Google ad libraries.**
- **Data Verifier (Ledger):** confirm no real website (Places `website` field, DNS checks on likely domains; social-only counts as no website); dedupe (normalised name + phone + postcode); UK → Companies House (`entity_type`); AU → ABN Lookup (disqualify cancelled); ad_refresh → disqualify big brands; enrich business contact details and timezone; apply country rules (§11). Business contact data only.
- **Copywriter (Quill):** `copy.json` (headline, subheadline, about, services[], cta, seo_title, meta_description). Locale spelling/currency. Never invent facts (reviews, awards, years, prices).
- **Creative Strategist (Lens):** `brief.md` with product/offer, audience, current strengths, weaknesses, 2–3 hooks, visual direction per variation, formats (1080×1080, 1080×1350, 1080×1920), brand elements to keep.
- **Graphic Designer (Iris/Juno):** website task → text logo + hero; ad task → 2–3 variations × 3 sizes. HTML/CSS templates in `templates/ads/` rendered to PNG with Playwright at exact sizes, using the business's **real product photos**. Never generate fake product images. Output `design_manifest.json`.
- **Web Developer (Pixel/Nova):** single-page static site from `templates/sites/` (trades, food & hospitality, beauty & wellness, retail, professional services), customised by style profile. Sections: hero, about, services, gallery (real images only), contact, footer. Mobile-first, accessible, `noindex` while in preview.
- **QA Tester (Hawk):**
  - Websites: no console errors at 375/768/1440 px (screenshots saved), no broken links/images, contact details match record exactly, no placeholder text, Lighthouse a11y ≥ 90 / perf ≥ 80 / SEO ≥ 80 (ignoring noindex), locale proofread, `noindex` present.
  - Ads: exact dimensions, min font size, WCAG AA contrast on key text, text-coverage threshold, logo + real product image present, proofread.
  - Output `qa_report.json` (`passed`, `issues[]` with severity/description/location/suggested fix). Any `high` = fail.
- **Deployment (Dock):** private noindexed Vercel preview using the **office's own** Vercel integration (internal org uses ours). Teardown after `preview_ttl_days` if `LOST`.
- **Outreach Specialist (Echo):** personalised draft from `templates/email/{workflow}.md`; website pitch includes preview link + screenshots; ad pitch includes watermarked before/after, framed as "variations you could A/B test". Applies country profile, checks suppression before drafting and before sending, sends via the **office's own connected mailbox** in the recipient's local window (Tue–Thu 9–11am default), respects daily caps, one follow-up then `LOST`. Opt-out replies → suppression immediately.
- **HR / Team Manager (new, Phase 6):**
  - On hire of any employee: runs an onboarding checklist (config valid, required integrations connected, test task passes) and reports readiness.
  - Weekly report per employee: items completed, QA first-pass rate, avg fix loops, avg time per state, credits used, cost per won deal.
  - Staffing suggestions based on queue depth and WIP saturation. Suggestions only — the CEO decides.

---

## 9. Data model

All tables have `id` (UUID), `created_at`, `updated_at`. **Every tenant table has `org_id` (FK, indexed) and every query filters on it.**

```
-- Tenancy & people
organizations     id, name, slug, plan_key, status[active|paused|cancelled], settings(json),
                  outreach_terms_accepted_at, is_internal(bool)
users             id, email, name, auth_provider_id
memberships       id, org_id, user_id, role[owner|ceo|manager|viewer], approvals_delegated(bool)
                  -- constraint: exactly one ceo per org

-- Catalogue (platform-level, no org_id)
employee_types    key, version, display_name, category, description, impl_path, task_kinds(json),
                  default_model, config_schema(json), risk_level, plans(json), est_credits_per_task, status
workflow_defs     key, version, definition(json)
office_templates  key, version, definition(json)
plans             key, name, price_monthly, currency, included_credits, max_employees,
                  max_workflows, allowed_employee_types(json), features(json)

-- Office setup
employees         id, org_id, type_key, type_version, name, avatar, config(json),
                  enabled, hired_at, fired_at
org_workflows     id, org_id, workflow_key, workflow_version, active, settings(json)
integrations      id, org_id, kind[email|vercel|places|companies_house|abn|webhook],
                  status, secret_encrypted, meta(json)

-- Work
work_items        id, org_id, workflow_key, kind[lead|...], status, assigned_employee_id,
                  fix_count, claimed_by, claim_expires_at, priority, disqualify_reason
lead_profiles     work_item_id (PK/FK), org_id, business_name, category, description, country,
                  region, timezone, address, phone, email, contact_name, website_found,
                  social_links(json), entity_type, registry_id, source, source_ref, preview_url
ad_captures       id, org_id, work_item_id, platform, ad_library_url, screenshot_paths(json),
                  ad_copy, first_seen, checklist(json), notes
artifacts         id, org_id, work_item_id, kind[copy|brief|logo|hero|site|ad_variant|qa_report|pitch],
                  path, version, created_by_employee_id, created_at
qa_reports        id, org_id, work_item_id, artifact_version, passed, issues(json)
approvals         id, org_id, work_item_id, kind[build|pitch|escalation], decision, notes,
                  decided_by_user_id, decided_at
outreach          id, org_id, work_item_id, kind[initial|followup], subject, body,
                  scheduled_for, sent_at, status, provider_message_id
suppression       id, org_id, email, domain, reason, added_at
events            id, org_id, work_item_id, from_status, to_status, actor_kind[employee|user|system],
                  actor_id, note, ts
jobs              id, org_id, work_item_id, employee_id, task, status, attempts, error, ts

-- Metering & billing
usage_events      id, org_id, employee_id, work_item_id, kind[llm|places_call|render|deploy|email_send],
                  model, input_tokens, output_tokens, cost_usd, credits, ts
credit_ledger     id, org_id, delta, reason[plan_grant|topup|usage|adjustment], ref, balance_after, ts
subscriptions     id, org_id, stripe_customer_id, stripe_subscription_id, plan_key, status,
                  current_period_end
audit_log         id, org_id, user_id, action, target, meta(json), ts   -- hires, fires, role changes, integration changes, approvals
```

Files: `data/orgs/{org_id}/items/{work_item_id}/` → `copy.json`, `brief.md`, `assets/`, `site/`, `ads/`, `qa/`, `pitch/`. A `FileStore` interface wraps this so we can move to S3/Supabase Storage in Phase 7.

Retention: `LOST`/`DISQUALIFIED` items have contact details purged after `retention_days` (default 180). Suppression entries are kept.

---

## 10. Metering & credits

- Every LLM call and billable action writes a `usage_events` row with `cost_usd` and `credits`.
- Credits = platform currency (e.g. 1 credit = fixed USD amount, set in `config/billing.yaml`).
- Phases 0–6: metering only (internal org has unlimited credits, but we still see costs per employee/task — this data sets SaaS pricing).
- Phase 8: plans grant monthly credits; top-ups via Stripe; Atlas pauses LLM work at zero balance.

---

## 11. Compliance & country rules

Not legal advice; the CEO reviews FTC, ICO and ACMA guidance before live sending. NZ's Unsolicited Electronic Messages Act applies to anything we send from NZ.

**Country profiles** (`config/countries/*.yaml`) — platform-enforced for every office:

```yaml
# us.yaml
code: US
spelling: en-US
currency: USD
channels_allowed: [email]            # no automated calls/SMS
contactable_entity_types: [any]
require_postal_address: true
require_unsubscribe: true
unsubscribe_honor_days: 10
send_window_local: {days: [TUE, WED, THU], start: "09:00", end: "11:00"}
```
```yaml
# uk.yaml
code: UK
spelling: en-GB
currency: GBP
channels_allowed: [email]
contactable_entity_types: [ltd, plc, llp]   # sole traders & ordinary partnerships → disqualify
require_postal_address: true
require_unsubscribe: true
require_source_disclosure: true
send_window_local: {days: [TUE, WED, THU], start: "09:00", end: "11:00"}
```
```yaml
# au.yaml
code: AU
spelling: en-AU
currency: AUD
channels_allowed: [email]
contactable_entity_types: [any]
require_conspicuous_publication: true
require_unsubscribe: true
unsubscribe_honor_days: 5
send_window_local: {days: [TUE, WED, THU], start: "09:00", end: "11:00"}
```

**Multi-tenant rules:**
- Offices send from **their own connected mailbox/domain**, never from platform infrastructure.
- Hiring an Outreach Specialist requires the owner to accept outreach terms (`outreach_terms_accepted_at`) and set a postal address for footers.
- Per-office suppression list; plus a platform-level block list for spam complaints and abuse.
- Platform daily send caps per office (start low, raise with account age and low complaint rate).
- Customer-facing lead sourcing uses official APIs only; the private scraper is internal-org-only.
- Audit log for approvals, hires, integration changes.

---

## 12. Internal dashboard (Phases 1–6, localhost)

Scoped to one office at a time (office switcher at the top):
- **Pipeline:** counts per workflow × status, per-employee load, today's spend/credits
- **Team:** hired employees, their type, config, status, current items; hire/fire/edit (CEO)
- **Approval queue:** site in iframe + 3 viewport screenshots, or ad variations beside the original; Hawk's report; Approve / Reject with notes / Disqualify
- **Pitch queue:** rendered email, editable; Approve / Edit / Reject
- **Escalations**, **Work item detail** (timeline + artifacts), **Intake** (ad captures, CSV import), **Suppression**, **Replies** (mark REPLIED/WON/LOST)
- **HR reports** (Phase 6)

All dashboard routes resolve `OrgContext` from the logged-in user's membership (Phase 1–6: a simple local login; Phase 7: Supabase Auth).

---

## 13. Customer web app (Phase 8)

Next.js on Vercel, calling the FastAPI API.

1. **Landing page:** what Wots Office is, employee catalogue, office templates, pricing.
2. **Sign up & subscribe:** Supabase Auth → Stripe Checkout for a plan.
3. **Onboarding wizard:**
   1. Name your office
   2. Choose the CEO (yourself or invite a teammate by email)
   3. Pick an office template (Web Agency / Ad Agency / Start empty)
   4. Review the recommended team → rename, remove, or add employees from the catalogue (limited by plan)
   5. Connect integrations required by your team (mailbox, Vercel, …) — HR/Team Manager's onboarding checklist shows what's missing
   6. Accept outreach terms (only if an Outreach Specialist is hired)
4. **Office app:** same pages as the internal dashboard, plus Billing (plan, credits, invoices) and Members (invite, roles, delegate approvals).

---

## 14. Repo layout

```
wots-office/
  docs/SPEC.md  docs/SPEC_v1.md
  config/settings.yaml  config/models.yaml  config/billing.yaml  config/countries/{us,uk,au}.yaml
  wots/
    core/          db.py models.py repo.py (org-scoped repositories) context.py config.py cli.py
                   events.py files.py jobs.py secrets.py metering.py
    orchestration/ atlas.py workflow_loader.py workflow_validator.py assignment.py timers.py
    employees/
      types/       *.yaml            # catalogue definitions
      impl/        lead_researcher.py ad_researcher.py data_verifier.py copywriter.py
                   creative_strategist.py graphic_designer.py web_developer.py qa_tester.py
                   deployment.py cold_email.py hr_manager.py
      base.py      # Employee protocol, EmployeeContext, EmployeeResult
    workflows/     website.yaml ad_refresh.yaml
    templates/offices/ web_agency.yaml ad_agency.yaml
    integrations/  existing_scraper.py places.py companies_house.py abn.py vercel.py
                   email_provider.py notify.py lighthouse.py stripe_client.py
    api/           (Phase 7) FastAPI routers: auth, orgs, members, employees, workflows,
                   work_items, approvals, billing, webhooks
    dashboard/     app.py templates/ static/
  web/             (Phase 8) Next.js app
  templates/       sites/ ads/ email/
  data/            wots.db orgs/ inbox/ outbox/
  tests/
  .env.example     # ANTHROPIC_API_KEY, SECRETS_KEY, GOOGLE_PLACES_KEY, COMPANIES_HOUSE_KEY, ABN_GUID,
                   # VERCEL_TOKEN, EMAIL_PROVIDER_KEY, NOTIFY_WEBHOOK, (Phase 7+) DATABASE_URL,
                   # SUPABASE_URL, SUPABASE_JWT_SECRET, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
```

---

## 15. Migration from v1 (do this first)

1. **Audit:** list which v1 phases/files exist and what tests pass. Report before editing.
2. **Tenancy tables:** add `organizations`, `users`, `memberships`. Alembic migration creates the internal org `Wots Office` (`slug: wots-office`, `is_internal: true`) and a user/membership for Lyndon as `owner` + `ceo`.
3. **Work items:** migrate `leads` → `work_items` (status, assignment, fix_count, claims) + `lead_profiles` (business/contact fields). `scope` → `workflow_key` (`website` | `ad_refresh`). Backfill `org_id` on every existing table.
4. **Workflows as data:** move the transition tables from `states.py` into `wots/workflows/*.yaml`; `states.py` becomes the loader/validator. Add the `VERIFY` state (existing `NEW` items owned by Ledger map to `VERIFY` if already claimed).
5. **Employees:** convert v1 agent classes into employee type implementations (`Pixel`/`Nova` → one `WebDeveloper`; `Iris`/`Juno` → one `GraphicDesigner`, etc.). Move `config/agents.yaml` entries into `employees` rows for the internal org via the office templates.
6. **Org scoping:** introduce `OrgContext` and org-scoped repositories; replace direct queries. Add a test helper that fails any query on a tenant table without an `org_id` filter.
7. **Files:** move `data/leads/{id}/` → `data/orgs/{org_id}/items/{id}/` behind `FileStore`.
8. **Metering:** wrap the LLM client so every call writes `usage_events`.
9. **Rename** `llm_usage` → `usage_events` (if it exists).
10. **Tests:** all v1 tests pass against the internal org; add isolation tests (§16 Phase 0).

---

## 16. Build phases

**Phase 0 — Multi-tenant core** (replaces v1 Phase 0; includes the migration)
Tenancy tables, org-scoped repos, employee type registry + loader, employees, workflow YAML loader/validator, office templates, work items, generic Atlas (assign/dispatch/fix/retry/leases/WIP batch+rolling), job queue interface, events, usage metering, CLI (`orgs create`, `employees hire`, `workflows validate`, `status`).
✅ Internal org seeded from `web_agency` + `ad_agency`. Tests: every allowed/blocked transition from YAML; batch vs rolling WIP; fix-loop escalation; lease expiry; `skip_if_missing`; activation fails when a required type isn't hired; **a second test org cannot read or modify the first org's items, files, employees or suppression list.**

**Phase 1 — Website workflow (no scraping, no email)**
CSV import → Verifier (enrichment only) → Copywriter → Web Developers → QA → approval queue in the internal dashboard.
✅ 4 sample leads reach `APPROVED`; a broken site gets `NEEDS_FIX`, returns to the same developer, re-passes; no developer exceeds WIP 2.

**Phase 2 — Real leads**
Lead Researcher (scraper adapter behind internal flag + Places API), Verifier checks (DNS, dedupe, Companies House, ABN), country rules.
✅ 50 real leads per country; UK sole traders disqualified with reason; real-domain businesses disqualified.

**Phase 3 — Assets & previews**
Graphic Designer logo/hero; Deployment previews (noindex, TTL cleanup) via org-level Vercel integration.
✅ Approved sites get working, non-indexable preview URLs.

**Phase 4 — Outreach (manual send)**
Outreach drafting, country footers, per-org suppression, pitch queue, scheduling, org-connected email provider. `auto_send=false`.
✅ Correct US/UK/AU pitches; suppressed addresses blocked at draft and send; sends land in the local window.

**Phase 5 — Ad Refresh workflow**
Intake, Ad Researcher, Creative Strategist, Graphic Designers (HTML→PNG, 3 sizes), ad QA, before/after pitch.
✅ 3 sample advertisers reach `PITCH_DRAFTED` with correct sizes and a passing QA report.

**Phase 6 — Hardening + HR / Team Manager**
Cost dashboard, budget guard, notifications, follow-ups, retention purge; HR onboarding checklist and weekly reports; win/reply metrics per workflow/country/employee.
✅ Weekly HR report generated for the internal org; hiring a new employee triggers the onboarding checklist.

**Phase 7 — SaaS foundations**
Postgres (Supabase) + RLS policies; Supabase Auth; public FastAPI API with org-scoped endpoints; real job queue; `FileStore` on cloud storage; encrypted per-org integrations with OAuth for Gmail/Microsoft mailboxes; per-org concurrency and send caps.
✅ Two real test orgs run workflows concurrently with full isolation (API, DB with RLS, files, queue).

**Phase 8 — Customer web app & billing**
Next.js landing/pricing, Stripe subscriptions + credit top-ups + webhooks, onboarding wizard (§13), office app, members/roles/invites, plan limits on hiring.
✅ A new customer can subscribe, name the office, set a CEO, pick a template, hire/rename employees, connect integrations, and see work flow to their approval queue; zero-credit offices pause correctly.

**Phase 9 — Catalogue growth**
`content_creator` employee + `content_studio` workflow/template; simple workflow editor (choose from validated building blocks, not free-form code).

---

## 17. Open decisions (ask the CEO)

1. Existing scraper interface (input/output format).
2. Email approach: internal org provider now; for customers, OAuth-connected Gmail/Microsoft mailboxes vs. SMTP. Note many transactional email providers forbid cold outreach.
3. Public unsubscribe: v1 uses "reply 'unsubscribe'" + `List-Unsubscribe: mailto:`; later a public endpoint.
4. Vercel preview structure: project per item vs. shared project with paths.
5. Our agency pricing per market (USD/GBP/AUD) for pitches.
6. Postal address for our email footers.
7. Model aliases (`fast`, `strong`) and daily LLM budget for the internal org.
8. SaaS plans: names, prices, included credits, employee limits (decide after Phase 6 cost data).
9. Credit value (USD per credit) and per-task credit estimates.
10. Product name for the SaaS (keep "Wots Office"?).
