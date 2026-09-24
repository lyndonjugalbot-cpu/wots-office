"""wots command line (spec §6).

  wots run [--port 8000]                     start the Atlas loop + dashboard
  wots tick                                  run a single tick (for debugging)
  wots import-leads leads.csv --scope website
  wots status                                pipeline counts per status and scope
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select

from .models import Lead
from .runtime import Runtime, build_runtime
from .states import ACTIVE_STATUSES, Scope, Status, statuses_for

CSV_FIELDS = {"business_name", "category", "description", "country", "region", "timezone", "address", "phone",
              "email", "contact_name", "website_found", "source_ref"}
COUNTRY_ALIASES = {"US": "US", "USA": "US", "UK": "UK", "GB": "UK", "AU": "AU", "AUS": "AU"}
STATUS_ORDER = list(Status)


def _banner(rt: Runtime) -> str:
    s = rt.config.settings
    return (f"DRY_RUN={'on' if s.dry_run else 'OFF'}  auto_send={'on' if s.auto_send else 'off'}  "
            f"agents enabled: {', '.join(sorted(rt.atlas.agents)) or 'none'}")


def cmd_run(rt: Runtime, args) -> int:
    """Atlas on a background schedule, plus the dashboard (spec §6: `wots run`)."""
    from apscheduler.schedulers.background import BackgroundScheduler

    every = rt.config.settings.atlas.tick_seconds
    print(_banner(rt))
    scheduler = BackgroundScheduler()

    def tick() -> None:
        report = rt.atlas.tick()
        if report and (report.assigned or report.ran or report.failures or report.escalated or report.fixes_routed):
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

        print(f"Dashboard: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
        # Localhost only: the dashboard can approve and disqualify leads
        uvicorn.run(create_app(rt), host="127.0.0.1", port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.shutdown(wait=False)
        print("Stopped.")
    return 0


def cmd_tick(rt: Runtime, _args) -> int:
    print(_banner(rt))
    report = rt.atlas.tick()
    print(report.summary() if report else "A tick is already running.")
    return 0


def import_csv(board, lines, filename: str, scope: str) -> tuple[int, list[str]]:
    """Create NEW leads from CSV rows. Shared by `wots import-leads` and the dashboard's Intake."""
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
            skipped.append(f"line {line}: country '{row.get('country')}' is not a v1 target (US, UK, AU)")
            continue
        fields = {k: v.strip() for k, v in row.items() if k in CSV_FIELDS - {"business_name", "country"} and v and v.strip()}
        board.create_lead(scope=scope, business_name=name, country=country, actor="import",
                          note=f"Imported from {filename} line {line}", source=f"csv:{filename}", **fields)
        created += 1
    return created, skipped


def cmd_import_leads(rt: Runtime, args) -> int:
    path = Path(args.csv)
    if not path.is_file():
        print(f"No such file: {path}", file=sys.stderr)
        return 1
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            created, skipped = import_csv(rt.board, f, path.name, args.scope)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Imported {created} lead(s) into scope '{args.scope}'.")
    for reason in skipped:
        print(f"  skipped {reason}")
    return 0


def cmd_status(rt: Runtime, _args) -> int:
    print(_banner(rt))
    with rt.sessions() as s:
        rows = s.execute(select(Lead.scope, Lead.status, func.count()).group_by(Lead.scope, Lead.status)).all()
        counts = {(scope, status): n for scope, status, n in rows}
        for scope in Scope:
            total = sum(n for (sc, _), n in counts.items() if sc == scope.value)
            print(f"\n{scope.value} ({total} lead{'s' if total != 1 else ''})")
            for status in sorted(statuses_for(scope), key=STATUS_ORDER.index):
                n = counts.get((scope.value, status.value), 0)
                if n:
                    print(f"  {status.value:<20} {n:>4}")
            if not total:
                print("  (empty)")
        designers = sorted({c.name for c in rt.config.agents.agents.values() if c.pool})
        print("\nDesigner load (active / max WIP)")
        for name in designers:
            active = s.scalar(select(func.count()).select_from(Lead).where(
                Lead.assigned_to == name, Lead.status.in_([st.value for st in ACTIVE_STATUSES])))
            print(f"  {name:<10} {active} / {rt.config.settings.wip.max_wip}")
    print(f"\nLLM spend today: ${rt.llm.spend_today():.2f} of ${rt.config.settings.llm_cap_usd:.2f} cap")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wots", description="Wots Office pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="start the Atlas loop and the dashboard")
    run.add_argument("--port", type=int, default=8000)
    run.add_argument("--no-dashboard", action="store_true")
    sub.add_parser("tick", help="run a single Atlas tick")
    imp = sub.add_parser("import-leads", help="import leads from a CSV file")
    imp.add_argument("csv")
    imp.add_argument("--scope", choices=[s.value for s in Scope], required=True)
    sub.add_parser("status", help="pipeline counts per status and scope")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    rt = build_runtime()
    handlers = {"run": cmd_run, "tick": cmd_tick, "import-leads": cmd_import_leads, "status": cmd_status}
    return handlers[args.command](rt, args)


if __name__ == "__main__":
    sys.exit(main())
