# Wots Office

An AI-employee office that finds small businesses, produces work for them on spec (a website, or refreshed social ads) and pitches that work by email. The CEO approves everything before it leaves the building.

Since spec v2 it is multi-tenant: every piece of data belongs to an **office**. Our own agency is the internal office "Wots Office" (`wots-office`), and later phases open the same platform to customers. The build spec is [docs/SPEC.md](docs/SPEC.md) (v1 is kept in `docs/SPEC_v1.md`). The v1 → v2 migration is described in [docs/MIGRATION_V2.md](docs/MIGRATION_V2.md).

| Phase | What | Status |
|---|---|---|
| 0 | Multi-tenant core: offices, users, roles, employee catalogue, workflows as YAML, org-scoped data with a query guard, FileStore, job queue, metering + credits, v1 migration | **Done** |
| 1 | Website workflow: CSV → Scout (pass-through) → Ledger → Quill → Iris → Pixel/Nova → Hawk → CEO approval | **Done**: acceptance test passes on v2 |
| 2 | Real leads: OpenStreetMap / Companies House research; verification (dedupe, no-website checks, Companies House, ABN Lookup, country rules) | **Built:** OpenStreetMap + Companies House (free). Needs the free Companies House / ABN keys; the scraper waits for its format |
| 3 | Assets & previews: logo, hero and share image; Dock publishes approved sites as noindexed Cloudflare Pages previews and takes down stale ones | **Built:** needs a free Cloudflare account for real previews |
| 4 | Outreach, manual send: Echo drafts compliant pitches, CEO pitch queue, sends in the recipient's window from your own mailbox, suppression, one follow-up, replies and opt-outs | **Built:** needs your postal address and a mailbox to send for real |
| 5 | Next. Ad Refresh workflow | |
| 6 | Hardening + HR / Team Manager | |
| 7–9 | SaaS foundations, customer app & billing, catalogue growth | |

## Quick start

Needs Python 3.12, [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m playwright install chromium     # Hawk's browser
(cd tools && npm install)                           # Lighthouse
(cd frontend && npm install && npm run build)       # the 3D office dashboard
cp .env.example .env                                # ANTHROPIC_API_KEY, SECRETS_KEY, WOTS_OWNER_EMAIL...

bin/wots run                  # Atlas every 30s + worker threads + dashboard on http://127.0.0.1:8000
```

`wots run` prints a **sign-in link**. Open it to get a 12-hour session; if you lose it, `bin/wots login-link` makes a new one (valid for 10 minutes). Then click **Load 4 sample leads** (or upload a CSV) and watch the office work. Click your own desk, or **Approvals**, to review sites.

The database is created and migrated automatically (`data/wots.db`). A fresh database starts with the internal office, its owner/CEO (from `WOTS_OWNER_EMAIL`), and the team from the `web_agency` and `ad_agency` templates.

**Safety defaults:** `DRY_RUN=true` and `auto_send: false`. In dry-run mode nothing is sent or deployed, notifications go to `data/outbox/{office}/notifications.log`, and LLM spend is capped at $1/day per office. Every site carries `noindex`.

### Commands

```bash
bin/wots status [--org wots-office]
bin/wots tick [--org wots-office]
bin/wots import-leads leads.csv --org wots-office --workflow website
bin/wots orgs create "Kiwi Web Co" --template web_agency --ceo boss@kiwi.test
bin/wots orgs list
bin/wots orgs accept-outreach-terms --org wots-office --postal-address "..."
bin/wots employees list --org wots-office
bin/wots employees hire --org wots-office --type web_developer --name Ada --config style_profile=minimal
bin/wots employees fire --org wots-office --name Ada
bin/wots workflows validate
bin/wots login-link [--email someone@example.com]
bin/wots research --country AU --trade hairdresser --regions "Geelong,Ballarat" --limit 50
bin/wots research --country UK --trade plumber --regions Manchester --source companies_house
bin/wots integrations connect --org some-customer --kind companies_house     # a customer office's own key
```

A lead CSV needs `business_name` and `country` (US, UK/GB or AU). The optional columns are `category`, `description`, `region`, `timezone`, `address`, `phone`, `email`, `contact_name`, `website_found` and `source_ref`.

For frontend development: run `wots run` and `cd frontend && npm run dev`, then open http://localhost:5173. The dev server proxies `/api` and `/auth` to port 8000.

## How it fits together

- **Offices and roles** (`wots/core/offices.py`, `context.py`):
  - Members are `owner`, `ceo` (exactly one per office), `manager` or `viewer`.
  - Human gates (approve, reject, escalations) need the CEO, or a manager the CEO delegated approvals to.
  - Hiring and firing need the CEO or owner.
- **Tenant isolation** (`wots/core/repo.py`):
  - Every tenant table has `org_id`, and code reads through `scoped()` / `one_or_404()`.
  - A session-wide query guard raises `TenancyViolation` on any query of a tenant table that doesn't filter by `org_id`.
  - Deliberate cross-office reads say so with `execution_options(cross_org=True)`.
- **Employees** are hired instances of a catalogue type:
  - Each type is a YAML file in `wots/employees/types/` (config schema, default model, risk level) with an implementation in `wots/employees/impl/`.
  - Pixel and Nova are two `web_developer` hires with different `style_profile`s.
  - An employee returns an `EmployeeResult`; it never changes an item's status itself.
  - Types built in later phases can be hired now. They wait at their desk.
- **Workflows are data** (`wots/workflows/*.yaml`): states with owners (`type`, `assign`, `assigned`, `gate`, `system`, `terminal`), transitions, WIP rules and the fix loop.
  - They're validated on load: reachability, exits, and that each `assign` state has exactly one next state.
  - They're also validated when an office activates them. For example, "The website workflow needs a QA Tester… Hire one or pick a different template."
- **Atlas** (`wots/orchestration/atlas.py`) is plain Python driven by the YAML. Each tick visits every office in turn, with a per-office job cap. For each office:
  1. **Budget and credits guard:** pause the employees that use Claude, and tell the CEO once.
  2. **Fix loop:** an item entering `NEEDS_FIX` for the 3rd time goes to `ESCALATED` instead.
  3. **Skip:** skip states whose type isn't hired and that are marked `skip_if_missing`.
  4. **Assignment:** assign within WIP limits.
     - `batch` mode takes new work only when everything current is done.
     - `rolling` mode refills as soon as there's a free slot.
     - Ties go to whoever had fewer items this week.
  5. **Dispatch:** claim items with a lease and queue a job. Failures back off 30s, 60s, then 120s, and escalate after 3 retries.
- **Files** live in `data/orgs/{org_id}/items/{item_id}/` behind `FileStore`, which refuses paths that escape the folder.
- **Metering** (`wots/core/metering.py`):
  - Every Claude call becomes a `usage_events` row, attributed to its office, employee and item, with a cost in USD and credits.
  - Customer offices also get a `credit_ledger` debit and pause at zero credits. The internal office is unlimited.
  - Model aliases and prices live in `config/models.yaml`.
- **Dashboard auth** (`wots/core/auth.py`):
  - HMAC-signed login links and session cookies.
  - The browser picks the office with the `X-Org` header.
  - Site previews use signed per-item file links, because sandboxed iframes send no cookie.

## Finding leads (Phase 2)

**Research:** `bin/wots research --country UK --trade hairdresser --regions "Manchester,Leeds"`, or **Find leads** in the dashboard's intake bar. The office's Lead Researcher searches a free source for a trade (`config/trades.yaml`) in each town. It skips businesses with a real website listed and ones already on the board, and puts the rest on the board at `NEW`.

| Source | Countries | Good for | Notes |
|---|---|---|---|
| **OpenStreetMap** (default) | US, UK, AU | Shopfront businesses: hairdressers, beauty salons, cafés, bakeries, florists, mechanics | Free, no key. Credit "© OpenStreetMap contributors". Tradespeople are barely mapped: a live check found 0 plumbers in Manchester. Only about 1 in 8 has a phone number, and almost none has an email. |
| **Companies House** | UK | Tradespeople and anyone else, by trade code (SIC) | Free; needs `COMPANIES_HOUSE_KEY`. Every hit is an active limited company, which is exactly who UK rules let us email. You get the registered address (often the accountant's) and no phone. |
| Google Places | US, UK, AU | | **Off.** Google's terms (Maps Platform ToS 3.2.3) forbid saving business names and addresses, which a lead list needs. Scraping the Google Maps website is also against its terms. The code stays behind `research.places_enabled` in case Google agrees otherwise. |
| Our scraper | internal office only | | Behind `internal_scraper_enabled`, waiting for its input/output format. |

Every request is recorded in `usage_events` and capped per office per day, because the OpenStreetMap servers are free community servers. If the main Overpass server is busy, research retries on a backup.

**Verification:** Ledger checks each lead in this order, and the first failure disqualifies it with a reason you can see in the lead's detail:
1. **Duplicate:** it's the same business as an earlier lead in this office (same name plus the same phone or postcode, or the same Places record).
2. **Already has a website:** Google lists a real site (a Facebook or Instagram page doesn't count), or a likely domain such as `joesplumbing.co.uk` shows their name or phone. Parked domains and other companies' sites don't count.
3. **UK, Companies House:** the business must be an active ltd, plc or llp. No match means it's probably a sole trader or partnership, which UK rules don't let us cold-email, so it's dropped.
4. **AU, ABN Lookup:** a cancelled ABN is dropped. No match is kept, since not every trading name is registered.
5. **Country rules:** the entity type must be contactable (`config/countries/*.yaml`).

**Keys:** add these free keys to `.env` for the internal office.
- `COMPANIES_HOUSE_KEY`: from developer.company-information.service.gov.uk.
- `ABN_GUID`: from abr.business.gov.au, Tools, Web services.

A missing or rejected key pauses only the work that needs it. Atlas tells you once a day, and the leads wait instead of failing.

**The existing scraper** is internal-only, behind the office setting `internal_scraper_enabled`, as the spec requires. It needs the scraper's input/output format before it can be wired in.

## Designs and previews (Phase 3)

**The Graphic Designer (Iris or Juno) draws each business's brand**, in the designer's style (`flat_brand`, `bold_playful` or `minimal`):
- a logo and a square mark, used for the favicon
- a decorative hero illustration made of brand colours, shapes and the trade's icon
- a 1200×630 share image for link previews in email and chat
- PNG copies for emails

It never makes up photos. The gallery stays empty until the business supplies real ones. The files and palette are listed in `design_manifest.json`.

**Dock publishes each approved site** as a preview on **Cloudflare Pages**. Its free plan allows commercial use; Vercel's free plan doesn't.
- All previews share one Pages project, and each site is a branch with a stable URL: `https://{business-name-id}.{project}.pages.dev`.
- Each preview is noindexed three ways: the page's robots tag, an `X-Robots-Tag` header and `robots.txt`.
- Dock loads the live URL and checks that it works and is noindexed before marking the lead `PREVIEW_DEPLOYED`.
- A site without a robots `noindex` tag is never published.
- In dry-run mode nothing is published. Dock keeps a local copy in `data/previews/`, and the lead gets no preview URL, so nothing downstream can send a link that doesn't work.
- A `LOST` lead's preview is taken down after `previews.preview_ttl_days` (30).

**Setup:**
1. Create a free Cloudflare account.
2. Make an API token with **Account → Cloudflare Pages → Edit** permission.
3. Put `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` in `.env`.
4. Run `cd tools && npm install`, which installs Cloudflare's `wrangler` CLI for uploads.

The Pages project is created the first time Dock deploys.

## Outreach (Phase 4)

**Getting ready:**
1. Accept the outreach terms and set the postal address for the email footers: `bin/wots orgs accept-outreach-terms --postal-address "..."`.
2. Hire Echo: `bin/wots employees hire --type cold_email --name Echo`.
3. Connect **your own** mailbox in `.env` (`SMTP_*`, `OUTREACH_FROM`, and optionally `IMAP_*`), then run `bin/wots mailbox check`. Most email services (SendGrid, Postmark, Mailgun and others) forbid cold outreach, so pitches go from your mailbox.

**The flow:**
1. **Echo drafts** each deployed site's pitch and its one follow-up.
   - Claude writes only the subject line and a one-line opener, from the lead's real details.
   - The body comes from `templates/email/`. The footer is added in code: who we are, the postal address, how to opt out, and (UK) where we found them.
   - Echo won't draft if the address is suppressed or there's no email. For UK leads we must say where we found them, and for AU leads the business must publish the address itself. If Echo can't tell, it asks you (escalates) to add that on the lead.
2. **You approve** in **Approvals → Pitches**. You can edit the subject and body, but the opt-out line must stay, or send it back with notes.
3. **Echo sends** the approved pitch in the recipient's local window (Tue–Thu 9–11am) and within the daily cap (20). Otherwise it's scheduled, not failed. Suppression is checked again just before sending.
4. **After sending:**
   - If there's no reply after 5 days, the approved follow-up goes out, threaded under the pitch.
   - After 5 more days the lead closes as LOST.
   - With IMAP set up, the mailbox is read every 5 minutes. Opt-outs ("unsubscribe", "not interested", "remove me") and bounces are suppressed at once and the lead is LOST. Real replies land in **Approvals → Replies** for you to close as Won or Lost. Without IMAP, mark replies by hand.
   - **Outreach → Never email** holds the suppression list. Addresses or whole company domains can be added; webmail domains like gmail.com can't be blocked as a whole.

**Dry run** saves every "sent" email as a `.eml` file in `data/outbox/{office}/emails/`. A pitch drafted in dry-run mode has a placeholder instead of a preview link, and it can never be sent for real.

## Tuning cost

Each employee's `model` and `effort` are part of their hire config: `bin/wots employees hire … --config effort=low`, or `update_employee`. To try settings on the 4 sample leads without touching the real office:

```bash
bin/wots trial opus-default
bin/wots trial sonnet-low --set Quill.model=claude-sonnet-5 --set Quill.effort=low
bin/wots compare opus-default sonnet-low      # writes data/trials/compare.html
```

Each trial runs the real pipeline (real Claude, browser and Lighthouse) in its own database under `data/trials/`, and reports the billed cost per lead. **Trials spend real credits.**

## Where the spec needed an interpretation

1. **Echo (Outreach Specialist) isn't hired yet.** High-risk types need the owner to accept the outreach terms first (spec §11).
   - The website workflow runs without Echo: `allow_missing: [cold_email]` is recorded with the reason, and items will wait at `PREVIEW_DEPLOYED`.
   - Run `wots orgs accept-outreach-terms`, then hire Echo.
2. **Start hop:** an `assigned` state with exactly one move into another `assigned` state is the "start work" step. So `ASSIGNED` and `NEEDS_FIX` both go to `BUILDING` when the developer picks the item up.
3. **Implicit escalation:** any employee-owned state may go to the workflow's `escalate_to` when retries run out. The CEO can resume an error escalation where it stopped, but not into `NEEDS_FIX` itself, and a send-back resets `fix_count`.
4. **Leads start at `NEW`,** which the lead researcher owns. Research runs create them there, and Scout passes each one, including CSV imports, on to `VERIFY`.
6. **UK leads with no Companies House match are dropped.** A limited company must be registered, so no match most likely means a sole trader or partnership. If a trading name differs from the registered name we lose a lead, which is the safe way round.
7. **Research skips businesses whose listing shows a real website.** Ledger still checks likely domains for the rest.
8. **Previews use Cloudflare Pages, not the spec's Vercel,** because Vercel's free plan forbids commercial use. The spec's open question (one project per site vs a shared project) is answered: one shared project, with a branch per site.
9. **Suppressed addresses can be dropped after approval:** `PREVIEW_DEPLOYED → DISQUALIFIED` and `PITCH_APPROVED → DISQUALIFIED` were added to the workflows (now version 2).
10. **You approve the follow-up together with the pitch,** so it can go out 5 days later without asking again.
11. **Dispatch finishes work first:** each tick's job budget goes to the latest pipeline steps first, so a big batch of new leads can't hold up approved sites or pitches.
12. **Google Places is off** because of its terms (see Finding leads). The spec asked for it; we switched it off after reading Google's terms.
5. **Dashboard:** it's still the 3D office (React + Three.js) on a JSON API, not HTMX, as agreed for v1. Local sign-in is a signed link; Supabase Auth replaces it in Phase 7.

## Tests

```bash
.venv/bin/python -m pytest -q            # all 169, about 2 minutes
.venv/bin/python -m pytest -q -m "not e2e"   # skip the browser/Lighthouse runs
```

Each test gets a real SQLite database (migrated with Alembic, so it starts with the internal office), a fake clock, fake employees or a scripted Claude, and a fake internet (`tests/fakeweb.py`: Places, Companies House, ABN Lookup, websites). Tests can never reach a real API. Coverage:
- every allowed and blocked transition from both YAML workflows
- broken workflow definitions are refused
- activation fails when a type is missing
- batch vs rolling WIP and the even split
- the fix loop escalates on the 3rd failure
- leases, retries and backoff
- `skip_if_missing`
- per-office job caps and round-robin
- the budget guard, credits and LLM costing
- a second office can't read or change the first office's items, files, employees or suppression list
- unscoped queries fail
- roles at the gates
- the dashboard API and office switching
- the v1 → v2 migration
- the CLI
- Phase 2: research paging, metering and the daily cap, and the scraper being internal-only
- Phase 4: per-country pitches and footers, suppression at draft and send, local send windows, the daily cap, dry-run outbox, follow-up then LOST, replies/opt-outs/bounces, the pitch queue
- Phase 3: the designer's files and styles; previews are published, checked for noindex, kept local in dry runs, and taken down after the TTL
- Phase 2 verification: 50 leads per country, with UK sole traders, real-domain businesses, cancelled ABNs and duplicates dropped with reasons
- Phase 1 acceptance: 4 sample leads reach APPROVED, a broken site is caught and fixed, a CEO rejection is revised, and WIP never goes above 2

## Troubleshooting

**`ModuleNotFoundError: No module named 'wots'` from `.venv/bin/wots`:** macOS (often iCloud Documents sync) can mark files in `.venv` as hidden, and Python 3.12 then skips the editable install's `.pth` file. Use `bin/wots`, or fix it with `chflags -R nohidden .venv`.

## The 3D office

`frontend/` (React + Three.js) is the dashboard. It's a Sims-style office with a desk for each employee:
- Monitors and status gems follow the live pipeline, and speech bubbles say who is working on what.
- Idle staff wander to the water cooler.
- Switch offices from the top bar. The Team tab lists the hires and lets the CEO hire or let people go.
- The approval queue previews each site at 375, 768 and 1440 px, next to the QA screenshots, Lighthouse scores and the copywriter's assumptions.
