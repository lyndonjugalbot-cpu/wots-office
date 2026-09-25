# Migration to spec v2: audit and plan

Written before any code changed, as spec v2 §15.1 asks.

## What v1 built (audit, 25 Sep 2026)

| v1 phase | Status | Where |
|---|---|---|
| 0 Skeleton | Done | `wots/core/` (config, models, states, board, atlas, llm, cli), Alembic `0001` |
| 1 Website pipeline | Done | `wots/agents/` (Ledger enrichment, Quill, Iris text logo, Pixel/Nova, Hawk), `templates/sites/`, `wots/dashboard/` (JSON API) + `frontend/` (3D office UI) |
| 2–6 | Not started | |

Tests: 57 unit/integration tests passing, plus 2 end-to-end tests (real browser + Lighthouse, scripted Claude).

Data on disk: `data/wots.db` holds 4 fictional sample leads (at IN_QA) and $0.098 of Quill usage from one accidental real run; `data/leads/1..4/` holds their files; `data/trials/` is empty.

Deviations from the spec that carry over into v2:
- The internal dashboard is the React/Three.js "3D office" backed by a FastAPI JSON API, not Jinja2 + HTMX (agreed with the CEO).
- Pixel/Nova build from templates without an LLM, and call Claude only to act on CEO notes or proofread issues.
- Iris's website task is a text logo + palette (no hero yet).

## v2 changes, mapped to v1 code

| v2 §15 step | v1 today | Change |
|---|---|---|
| 2 Tenancy | none | `organizations`, `users`, `memberships`; migration seeds "Wots Office" (`wots-office`, internal) with the owner as owner + CEO |
| 3 Work items | `leads` | `work_items` + `lead_profiles`; `scope` -> `workflow_key`; `org_id` everywhere; UUID ids |
| 4 Workflows as data | `wots/core/states.py` tables | `wots/workflows/website.yaml`, `ad_refresh.yaml`; loader + validator; new `VERIFY` state |
| 5 Employees | `config/agents.yaml` + `wots/agents/` | Employee type catalogue (`wots/employees/types/*.yaml`) + impls (`wots/employees/impl/`); `employees` rows hired from office templates |
| 6 Org scoping | direct queries | `OrgContext` + org-scoped repository; a test guard fails any tenant-table query without `org_id` |
| 7 Files | `data/leads/{id}/` | `data/orgs/{org_id}/items/{id}/` behind `FileStore` |
| 8–9 Metering | `llm_usage` | `usage_events` with credits |
| 10 Tests | 57 + 2 | all ported to the internal org, plus tenant isolation tests |

## Result (25 Sep 2026)

Spec v2 Phase 0 (multi-tenant core) is done, and Phase 1 still passes on top of it: 107 tests, including the Phase 1 end-to-end run with a real browser and Lighthouse.

**Real data migrated:**
- `data/wots.db` was upgraded to revision `0002`, after a rehearsal on a copy. The backup is `backups/v1-before-v2-20260925-130350/`.
- The 4 sample leads are now work items in "Wots Office", still at IN_QA with their developers (Nova, Pixel).
- Their files moved to `data/orgs/{org}/items/{id}/`.
- The $0.0983 of usage is now 9.8 credits in `usage_events`.
- The owner/CEO is `WOTS_OWNER_EMAIL` from `.env`.

**Found and fixed along the way:**
- **Fix loop:** an item could be handed back to its developer in the same tick it reached the fix-loop max. Dispatch now escalates it instead.
- **Office templates:** creating an office from `web_agency` failed because Echo (high risk) can't be hired before the outreach terms are accepted. Terms-blocked types are now recorded as `allow_missing`, with the reason.
- **Migration drop order:** the migration dropped `v1_leads` before the tables that reference it. SQLite's foreign-key check would have rejected that on any database with artifacts.
- **Migration URL:** `migrations/env.py` let `DATABASE_URL` override the URL the runtime passed in. A trial or test runtime could have migrated the wrong database.
- **Sandboxed previews:** a previewed site couldn't load its logo, because a sandboxed iframe sends no session cookie. Files are now served through signed per-item links.

**Still open:** the spec §17 decisions, in particular the outreach terms and postal address (needed before Echo can be hired), and model aliases and budget for the internal office.
