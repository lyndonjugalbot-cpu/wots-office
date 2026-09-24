# Wots Office

An AI-agent office that finds small businesses, produces work for them on spec (a website, or refreshed social ads) and pitches that work by email. The CEO approves everything before it leaves the building.

The full build spec is in [docs/SPEC.md](docs/SPEC.md). It is built phase by phase, and each phase must pass its acceptance criteria before the next starts.

| Phase | What | Status |
|---|---|---|
| 0 | Skeleton: config, DB + migrations, state tables, board, Atlas (leases, WIP, fixes, retries, budget guard), events, CLI, DRY_RUN | **Done** |
| 1 | Website pipeline: CSV import → Ledger → Quill → Iris (text logo) → Pixel/Nova → Hawk → approval queue | **Done**: acceptance test passes (49 tests in all) |
| 2 | Next. Real leads: Scout, Places API, Companies House, ABN Lookup, country rules | |
| 3 | Assets & previews: Iris, Dock (Vercel, noindex) | |
| 4 | Outreach (manual send): Echo, suppression, pitch queue | |
| 5 | Ad Refresh scope | |
| 6 | Hardening: costs dashboard, follow-ups, retention, metrics | |

## Quick start

Needs Python 3.12, [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m playwright install chromium     # Hawk's browser
(cd tools && npm install)                           # Lighthouse
(cd frontend && npm install && npm run build)       # the 3D office dashboard
cp .env.example .env                                # add ANTHROPIC_API_KEY (and ANTHROPIC_WORKSPACE_ID if your key needs one)

.venv/bin/wots run            # Atlas every 30s + dashboard at http://127.0.0.1:8000
```

Then open http://127.0.0.1:8000, click **Load 4 sample leads** (or upload a CSV), and watch the office work. Click your own desk, or **Approvals**, to review sites. Other commands: `wots import-leads leads.csv --scope website`, `wots status`, `wots tick`.

The database is created and migrated automatically (`data/wots.db`). A lead CSV needs `business_name` and `country` (US, UK/GB or AU). Optional columns: `category`, `description`, `region`, `timezone`, `address`, `phone`, `email`, `contact_name`, `website_found`, `source_ref`.

**Safety defaults:** `DRY_RUN=true` and `auto_send: false`. In dry-run mode nothing is sent or deployed, notifications go to `data/outbox/notifications.log`, and LLM spend is capped at `dry_run_llm_cap_usd` ($1/day). Every site carries `noindex`.

For frontend development: run `wots run` and `cd frontend && npm run dev`, then open http://localhost:5173. The dev server proxies `/api` to port 8000.

## The website pipeline (Phase 1)

| Agent | What it does | Uses Claude? |
|---|---|---|
| Ledger | Enriches: local timezone, phone in the country's format, tidy email. (Verification is Phase 2.) | No |
| Quill | Writes `copy.json` in local spelling. Never invents facts, and lists any services it had to assume, which the CEO sees at approval. | Yes (strong model) |
| Iris | Brand palette plus a text logo, darkened until white text passes WCAG AA. (Real design is Phase 3.) | No |
| Pixel / Nova | Build the site from `templates/sites/` (5 category templates) in their style profile. On CEO notes or proofread issues they revise the copy with Claude; mechanical QA failures just get a clean rebuild. | Only to act on feedback |
| Hawk | Checks the page at 375, 768 and 1440 px: console errors, broken links and images, contact details matching the lead exactly, placeholder text, noindex, Lighthouse (a11y ≥ 90, perf ≥ 80, SEO ≥ 80, ignoring noindex), and a proofread. Any high issue sends the site back. | Proofread only (fast model) |

The **approval queue** shows:
- a live preview you can switch between 375, 768 and 1440 px
- Hawk's screenshots, Lighthouse scores and issues
- Quill's assumptions
- Approve, Reject (your notes go to the designer) and Disqualify

The **Escalated** tab offers "send back to the designer", "retry where it stopped" and "drop".

If the Claude key or workspace is wrong, Atlas pauses the Claude-using agents, tells you once, and leaves the leads where they are instead of escalating them all.

## How it fits together

- **The board is the source of truth** (`wots/core/board.py`). Every lead is one row with a status. `board.transition()` checks the scope's table in `wots/core/states.py` and writes an `events` row for every change.
- **Atlas is plain Python** (`wots/core/atlas.py`). Each tick, in order:
  1. **Budget guard:** pause LLM agents once today's spend reaches the cap, and notify the CEO once.
  2. **Fix routing:** send a `NEEDS_FIX` lead back to the same designer with the latest QA report or CEO notes. At `fix_count` 3 it's `ESCALATED`.
  3. **Assignment:**
     - `batch` WIP mode gives a designer new work only when all its leads are approved.
     - `rolling` mode refills as soon as it's below `max_wip`.
     - When several designers are free, the one with fewer leads this week gets the work.
  4. **Dispatch:** each enabled agent claims leads with a lease. A crashed run's lead is freed when the lease expires. Failures back off exponentially (30s, 60s, 120s) and escalate after 3 retries.
- **Agents** implement `run(lead, ctx) -> AgentResult` and never change status themselves (`wots/agents/base.py`). They're switched on in `config/agents.yaml` as each phase lands. Pixel/Nova and Iris/Juno share one class each, so another designer is just a config entry.
- **Every LLM call is costed** into `llm_usage` (`wots/core/llm.py`) using the prices in `config/settings.yaml`. "Today" is the CEO's day in Auckland.

## Where the spec needed an interpretation

These are small calls I made where the spec was silent or two sections pulled in different directions. They're easy to change:

1. **Extra transitions** (documented at the top of `states.py`):
   - `READY_FOR_APPROVAL → DISQUALIFIED`, for the approval queue's Disqualify button.
   - `ad_refresh` gets the same escalation exits and `PITCHED → LOST` as `website`.
   - Any working status can go to `ESCALATED` after retries run out.
   - The CEO can resume an error escalation at the status it came from.
2. **Fix count:** Atlas increments `fix_count` when routing a fix, and escalates when it reaches 3. So the third QA failure escalates.
3. **Retries:** 3 retries after the first failure (4 attempts in total), then escalate.
4. **CEO send-back:** sending an escalated lead back resets `fix_count` to 0, so it gets a fresh set of fix attempts.
5. **Agent config:** agents declare `owns: {scope: status}` instead of a single `owns_status`, because Iris, Echo and Ledger work different statuses in each scope. Agent results can also carry `updates`, which Ledger needs for enrichment.

## Tests

```bash
.venv/bin/python -m pytest -q
```

They use a real SQLite database per test (migrated with Alembic), a fake clock, and fake agents or a scripted Claude, so they never call the real API or cost anything. The Phase 1 acceptance test (`pytest -m e2e`, about a minute) runs the real agents, browser and Lighthouse. They cover:
- every allowed and blocked transition in both scopes
- batch vs rolling WIP, the even split, and WIP never going above 2
- fix routing and escalation
- lease expiry and crashed agents
- retry backoff
- the budget guard and LLM costing
- dry-run notifications
- migrations matching the models
- the CLI
- the dashboard API
- Phase 1 acceptance: 4 sample leads reach APPROVED, a broken site is caught and fixed, a CEO rejection is revised, and WIP never goes above 2

## Troubleshooting

**`ModuleNotFoundError: No module named 'wots'` from `.venv/bin/wots`:** macOS (often iCloud Documents sync) can mark files in `.venv` as hidden, and Python 3.12 then skips the editable install's `.pth` file. Fix it with:

```bash
chflags -R nohidden .venv
```

## The 3D office

`frontend/` (React + Three.js) is the dashboard: a Sims-style office where each agent sits at a desk. Monitors glow and status gems change colour with the live pipeline, speech bubbles say who is working on which business, and the whiteboard shows the pipeline counts. It replaced the spec's suggested HTMX pages, which was a deliberate choice. All the data comes from the FastAPI JSON API in `wots/dashboard/app.py`, so an HTMX version could sit on the same API later.
