"""wots command line (spec v2 §7).

  wots run [--port 8000]                       Atlas + workers + internal dashboard
  wots tick [--org wots-office]                one Atlas tick
  wots import-leads leads.csv --org wots-office --workflow website
  wots status [--org wots-office]
  wots orgs create "Name" --template web_agency [--template ad_agency] --ceo you@example.com
  wots orgs list | wots orgs accept-outreach-terms --org X --postal-address "..."
  wots employees hire --org X --type web_developer --name Pixel --config style_profile=clean_modern
  wots employees list --org X | wots employees fire --org X --name Pixel
  wots workflows validate
  wots research --country UK --trade plumber --regions "Manchester,Leeds" [--source osm|companies_house]
  wots integrations connect --org X --kind places|companies_house|abn   (asks for the key)
  wots login-link [--email you@example.com]    a sign-in link for the dashboard
  wots trial NAME [--set Pixel.effort=low]     try model/effort settings on the sample leads
  wots compare NAME NAME ...                   side-by-side report of trials
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select

from .context import OrgContext
from .models import WorkItem
from .runtime import Runtime, build_runtime

CSV_FIELDS = {"category", "description", "region", "timezone", "address", "phone", "email", "contact_name",
              "website_found", "source_ref"}
COUNTRY_ALIASES = {"US": "US", "USA": "US", "UK": "UK", "GB": "UK", "AU": "AU", "AUS": "AU"}
INTERNAL = "wots-office"


def import_csv(rt: Runtime, ctx: OrgContext, lines, filename: str, workflow: str) -> tuple[int, list[str]]:
    """Create work items from CSV rows. Shared by `wots import-leads` and the dashboard's Intake."""
    if workflow not in {w.workflow_key for w in rt.offices.workflows(ctx) if w.active}:
        raise ValueError(f"The {workflow} workflow isn't active in {ctx.name}")
    created, skipped = 0, []
    reader = csv.DictReader(lines)
    missing = {"business_name", "country"} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
    for line, row in enumerate(reader, start=2):
        name = (row.get("business_name") or "").strip()
        country = COUNTRY_ALIASES.get((row.get("country") or "").strip().upper())
        if not name:
            skipped.append(f"line {line}: no business_name")
            continue
        if not country:
            skipped.append(f"line {line}: country '{row.get('country')}' is not a target (US, UK, AU)")
            continue
        fields = {k: v.strip() for k, v in row.items() if k in CSV_FIELDS and v and v.strip()}
        rt.board.create_item(ctx, workflow_key=workflow, note=f"Imported from {filename} line {line}",
                             profile={"business_name": name, "country": country, "source": f"csv:{filename}", **fields})
        created += 1
    return created, skipped


def _banner(rt: Runtime) -> str:
    s = rt.config.settings
    return (f"DRY_RUN={'on' if s.dry_run else 'OFF'}  auto_send={'on' if s.auto_send else 'off'}  "
            f"offices: {', '.join(o.slug for o in rt.offices.orgs()) or 'none'}")


def cmd_run(rt: Runtime, args) -> int:
    """Atlas on a background schedule with a worker pool, plus the dashboard."""
    from apscheduler.schedulers.background import BackgroundScheduler

    from .auth import login_link
    from .jobs import ThreadQueue

    rt.atlas.queue = ThreadQueue(rt.config.settings.atlas.workers)
    every = rt.config.settings.atlas.tick_seconds
    print(_banner(rt))
    scheduler = BackgroundScheduler()

    def tick() -> None:
        report = rt.atlas.tick()
        if report and (report.assigned or report.ran or report.failures or report.escalated):
            print(report.summary(), flush=True)

    scheduler.add_job(tick, "interval", seconds=every, max_instances=1, coalesce=True)
    scheduler.start()
    print(f"Atlas ticking every {every}s.")
    try:
        if args.no_dashboard:
            import time
            while True:
                time.sleep(3600)
        import uvicorn

        from ..dashboard.app import create_app

        link = login_link(rt, None, f"http://127.0.0.1:{args.port}")
        print(f"Dashboard: {link}\n  (a sign-in link for the internal office's owner; valid for 10 minutes)")
        # Localhost only: the dashboard can approve and disqualify work
        uvicorn.run(create_app(rt), host="127.0.0.1", port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.shutdown(wait=False)
        print("Stopped.")
    return 0


def cmd_tick(rt: Runtime, args) -> int:
    print(_banner(rt))
    report = rt.atlas.tick(args.org)
    print(report.summary() if report else "A tick is already running.")
    return 0


def cmd_import_leads(rt: Runtime, args) -> int:
    path = Path(args.csv)
    if not path.is_file():
        print(f"No such file: {path}", file=sys.stderr)
        return 1
    ctx = rt.offices.system_ctx(args.org)
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            created, skipped = import_csv(rt, ctx, f, path.name, args.workflow)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Imported {created} lead(s) into {ctx.name} ({args.workflow}).")
    for reason in skipped:
        print(f"  skipped {reason}")
    return 0


def cmd_status(rt: Runtime, args) -> int:
    print(_banner(rt))
    orgs = [rt.offices.org(args.org)] if args.org else rt.offices.orgs()
    for org in orgs:
        ctx = rt.offices.system_ctx(org.id)
        print(f"\n== {org.name} ({org.slug}{', internal' if org.is_internal else ''})")
        with rt.sessions() as s:
            rows = s.execute(select(WorkItem.workflow_key, WorkItem.status, func.count()).where(
                WorkItem.org_id == ctx.org_id).group_by(WorkItem.workflow_key, WorkItem.status)).all()
        for ow in rt.offices.workflows(ctx):
            wf = rt.catalogue.workflows[ow.workflow_key]
            counts = {st: n for key, st, n in rows if key == ow.workflow_key}
            print(f"  {ow.workflow_key} ({'active' if ow.active else 'inactive'}, {sum(counts.values())} items)")
            for state in wf.states:
                if counts.get(state):
                    print(f"    {state:<20} {counts[state]:>4}")
            if ow.settings.get("allow_missing"):
                print(f"    waiting for: {', '.join(ow.settings['allow_missing'])}"
                      + (f" ({ow.settings['missing_reason']})" if ow.settings.get("missing_reason") else ""))
        print("  Team:")
        for st in rt.atlas.staff(ctx):
            extra = "" if st.impl else "  (arrives in a later phase)"
            print(f"    {st.info.name:<10} {st.info.type.display_name}{extra}")
        print(f"  LLM spend today: ${rt.meter.spend_today(ctx):.2f} of ${rt.meter.daily_cap(ctx):.2f} cap")
    return 0


def cmd_orgs(rt: Runtime, args) -> int:
    from .offices import OfficeError

    try:
        if args.orgs_cmd == "create":
            ctx = rt.offices.create_office(args.name, templates=args.template, ceo_email=args.ceo,
                                           owner_email=args.owner, slug=args.slug,
                                           allow_missing=set(args.allow_missing or []))
            print(f"Created {ctx.name} ({ctx.slug}).")
            for w in rt.offices.workflows(ctx):
                waiting = w.settings.get("allow_missing")
                print(f"  workflow {w.workflow_key}: active" + (f", waiting for {', '.join(waiting)}" if waiting else ""))
        elif args.orgs_cmd == "list":
            for org in rt.offices.orgs():
                print(f"{org.slug:<20} {org.name}{'  (internal)' if org.is_internal else ''}  [{org.status}]")
        elif args.orgs_cmd == "accept-outreach-terms":
            rt.offices.accept_outreach_terms(rt.offices.system_ctx(args.org), args.postal_address)
            print("Outreach terms accepted. You can now hire an Outreach Specialist.")
    except OfficeError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_employees(rt: Runtime, args) -> int:
    from .offices import OfficeError

    ctx = rt.offices.system_ctx(args.org)
    try:
        if args.emp_cmd == "hire":
            config = dict(kv.split("=", 1) for kv in args.config or [])
            emp = rt.offices.hire(ctx, args.type, args.name, config)
            print(f"Hired {emp.name} ({args.type}) with {emp.config}")
        elif args.emp_cmd == "list":
            for emp in rt.offices.employees(ctx):
                print(f"{emp.name:<10} {emp.type_key:<20} {'enabled' if emp.enabled else 'disabled'}  {emp.config}")
        elif args.emp_cmd == "fire":
            emp = next((e for e in rt.offices.employees(ctx) if e.name == args.name), None)
            if not emp:
                raise OfficeError(f"No employee called {args.name}")
            rt.offices.fire(ctx, emp.id)
            print(f"{args.name} has left the office.")
    except OfficeError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_workflows(rt: Runtime, _args) -> int:
    for key, wf in rt.catalogue.workflows.items():
        print(f"{key} v{wf.version}: valid ({len(wf.states)} states, {len(wf.transitions)} transitions)")
    return 0


def cmd_login_link(rt: Runtime, args) -> int:
    from .auth import login_link

    print(login_link(rt, args.email, f"http://127.0.0.1:{args.port}"))
    return 0


def cmd_trial(_rt, args) -> int:
    from .trials import run_trial

    try:
        run_trial(args.name, args.set or [])
    except (ValueError, RuntimeError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_compare(_rt, args) -> int:
    from .trials import compare

    try:
        print(f"Open {compare(args.names)}")
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_research(rt: Runtime, args) -> int:
    from ..integrations.errors import IntegrationError
    from ..orchestration.research import MAX_PLACES_PAGES, SOURCES, ResearchError, run_research

    regions = [r.strip() for r in (args.regions or "").split(",") if r.strip()]
    print(f"Searching {SOURCES[args.source]} for {args.trade} in {args.country}"
          f"{' (' + ', '.join(regions) + ')' if regions else ''}: up to {args.limit} leads.")
    if args.source == "places" and rt.config.settings.research.places_enabled:
        settings = rt.config.settings.research
        most = min(len(regions or [None]) * MAX_PLACES_PAGES, settings.max_places_requests_per_day)
        print(f"Google bills this: at most {most} requests (~${most * settings.places_cost_per_request_usd:.2f}).")
        if not args.yes:
            if not sys.stdin.isatty():
                print("Add --yes to run this without a prompt.", file=sys.stderr)
                return 1
            if input("Go ahead? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("Cancelled.")
                return 1
    ctx = rt.offices.system_ctx(args.org)
    try:
        report = run_research(rt, ctx, country=args.country, trade=args.trade, regions=regions, limit=args.limit,
                              source=args.source)
    except (ResearchError, IntegrationError) as e:
        print(e, file=sys.stderr)
        return 1
    print(report.summary())
    if args.source == "osm":
        print("Data © OpenStreetMap contributors (ODbL).")
    print("Atlas verifies them on its next ticks (`wots tick`, or `wots run`).")
    return 0


def cmd_integrations(rt: Runtime, args) -> int:
    import getpass

    from ..core.secrets import SecretsError

    ctx = rt.offices.system_ctx(args.org)
    secret = getpass.getpass(f"{args.kind} key for {ctx.name} (not shown): ").strip()
    if not secret:
        print("No key given.", file=sys.stderr)
        return 1
    try:
        rt.integrations.connect(ctx, args.kind, secret)
    except SecretsError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Saved {ctx.name}'s {args.kind} key (encrypted).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wots", description="Wots Office")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Atlas + workers + dashboard")
    run.add_argument("--port", type=int, default=8000)
    run.add_argument("--no-dashboard", action="store_true")
    tick = sub.add_parser("tick", help="one Atlas tick")
    tick.add_argument("--org")
    imp = sub.add_parser("import-leads", help="import leads from a CSV file")
    imp.add_argument("csv")
    imp.add_argument("--org", default=INTERNAL)
    imp.add_argument("--workflow", default="website")
    status = sub.add_parser("status", help="pipeline counts, team and spend")
    status.add_argument("--org")

    orgs = sub.add_parser("orgs", help="offices").add_subparsers(dest="orgs_cmd", required=True)
    create = orgs.add_parser("create")
    create.add_argument("name")
    create.add_argument("--template", action="append", required=True)
    create.add_argument("--ceo", required=True, help="the CEO's email")
    create.add_argument("--owner", help="the owner's email (defaults to the CEO)")
    create.add_argument("--slug")
    create.add_argument("--allow-missing", action="append", metavar="TYPE",
                        help="activate workflows even though this employee type isn't hired (repeatable)")
    orgs.add_parser("list")
    terms = orgs.add_parser("accept-outreach-terms")
    terms.add_argument("--org", default=INTERNAL)
    terms.add_argument("--postal-address", required=True)

    emps = sub.add_parser("employees", help="hire, list and fire employees").add_subparsers(dest="emp_cmd", required=True)
    hire = emps.add_parser("hire")
    hire.add_argument("--org", default=INTERNAL)
    hire.add_argument("--type", required=True)
    hire.add_argument("--name", required=True)
    hire.add_argument("--config", action="append", metavar="KEY=VALUE")
    lst = emps.add_parser("list")
    lst.add_argument("--org", default=INTERNAL)
    fire = emps.add_parser("fire")
    fire.add_argument("--org", default=INTERNAL)
    fire.add_argument("--name", required=True)

    research = sub.add_parser("research", help="find leads (OpenStreetMap, or Companies House for the UK)")
    research.add_argument("--org", default=INTERNAL)
    research.add_argument("--country", required=True, choices=["US", "UK", "AU"])
    research.add_argument("--trade", required=True, help="a trade from config/trades.yaml, e.g. plumber")
    research.add_argument("--regions", required=True, help="comma-separated towns, e.g. 'Manchester,Leeds'")
    research.add_argument("--limit", type=int, default=50)
    research.add_argument("--source", default="osm", choices=["osm", "companies_house", "places", "scraper"])
    research.add_argument("--yes", action="store_true", help="don't ask before spending (Places only)")
    integ = sub.add_parser("integrations", help="an office's API keys").add_subparsers(dest="int_cmd", required=True)
    connect = integ.add_parser("connect")
    connect.add_argument("--org", default=INTERNAL)
    connect.add_argument("--kind", required=True, choices=["places", "companies_house", "abn"])

    wfs = sub.add_parser("workflows", help="workflow definitions").add_subparsers(dest="wf_cmd", required=True)
    wfs.add_parser("validate")
    link = sub.add_parser("login-link", help="print a dashboard sign-in link")
    link.add_argument("--email")
    link.add_argument("--port", type=int, default=8000)

    trial = sub.add_parser("trial", help="try model/effort settings on the sample leads, in a separate database")
    trial.add_argument("name")
    trial.add_argument("--set", action="append", metavar="EMPLOYEE.FIELD=VALUE",
                       help="e.g. --set Quill.model=claude-sonnet-5 --set Quill.effort=low (repeatable)")
    cmp = sub.add_parser("compare", help="side-by-side HTML report of finished trials")
    cmp.add_argument("names", nargs="+")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.command in {"trial", "compare"}:  # these use their own trial databases
        return {"trial": cmd_trial, "compare": cmd_compare}[args.command](None, args)
    from .repo import NotFound

    rt = build_runtime()
    handlers = {"run": cmd_run, "tick": cmd_tick, "import-leads": cmd_import_leads, "status": cmd_status,
                "orgs": cmd_orgs, "employees": cmd_employees, "workflows": cmd_workflows, "login-link": cmd_login_link,
                "research": cmd_research, "integrations": cmd_integrations}
    try:
        return handlers[args.command](rt, args)
    except NotFound as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
