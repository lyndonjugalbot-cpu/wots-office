"""Model/effort trials: run the sample leads through the real pipeline with chosen settings, in a
separate database, and compare the results side by side (copy, screenshots, QA, real cost).

  bin/wots trial opus-high
  bin/wots trial sonnet-low --set Quill.model=claude-sonnet-5 --set Quill.effort=low
  bin/wots compare opus-high sonnet-low        -> data/trials/compare.html
"""
from __future__ import annotations

import html
import json
import shutil
import time
from pathlib import Path

from sqlalchemy import func, select

from .config import ROOT, Config, get_config
from .models import UsageEvent
from .offices import OfficeError
from .runtime import build_runtime

SAMPLES = ROOT / "samples" / "phase1_leads.csv"
SETTLED = {"READY_FOR_APPROVAL", "APPROVED", "ESCALATED", "DISQUALIFIED"}
OFFICE = "wots-office"  # every fresh database starts with the internal office and its team


def trials_root(config: Config) -> Path:
    return config.settings.data_path / "trials"


def parse_overrides(overrides: list[str]) -> dict[str, dict]:
    """`Quill.model=claude-sonnet-5` -> {"Quill": {"model": "claude-sonnet-5"}} (by hired employee name)."""
    out: dict[str, dict] = {}
    for item in overrides:
        key, _, value = item.partition("=")
        name, _, field = key.partition(".")
        if not name or not field or not value:
            raise ValueError(f"Can't apply '{item}'. Use Employee.field=value, e.g. Quill.effort=low")
        out.setdefault(name, {})[field] = None if value in {"none", "default"} else value
    return out


def apply_overrides(rt, ctx, overrides: dict[str, dict]) -> None:
    """Change the named employees' config in the trial office. The type's config schema validates each
    change (unknown fields, bad effort levels), and every model must be priced in config/models.yaml."""
    staff = {e.name: e for e in rt.offices.employees(ctx)}
    for name, changes in overrides.items():
        emp = staff.get(name)
        if not emp:
            raise ValueError(f"No employee called {name}. The team is: {', '.join(sorted(staff))}")
        model = changes.get("model")
        if model and rt.config.models.resolve(model) not in rt.config.models.pricing:
            raise ValueError(f"{model} has no price in config/models.yaml")
        try:
            rt.offices.update_employee(ctx, emp.id, changes)
        except OfficeError as e:
            raise ValueError(f"{name}: {e}") from e


def model_line(rt, ctx) -> dict[str, dict]:
    out = {}
    for st in rt.atlas.staff(ctx):
        if st.info.type.uses_llm and st.impl is not None:
            model = rt.config.models.resolve(st.info.config.get("model") or st.info.type.default_model)
            out[st.info.name] = {"model": model, "effort": st.info.config.get("effort")}
    return out


def run_trial(name: str, overrides: list[str], *, config: Config | None = None, llm_client=None,
              max_minutes: float = 15, echo=print, http_transport=None) -> dict:
    base = config or get_config()
    folder = trials_root(base) / name
    if folder.exists():
        raise ValueError(f"Trial '{name}' already exists in {folder}. Pick another name or delete that folder.")
    parsed = parse_overrides(overrides)
    trial = base.model_copy(deep=True)
    trial.settings = trial.settings.model_copy(update={
        "data_dir": str(folder), "database_url": f"sqlite:///{folder / 'wots.db'}"})
    folder.mkdir(parents=True)
    try:
        return _run(name, folder, trial, parsed, llm_client, max_minutes, echo, http_transport)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # a failed trial leaves nothing behind, so the name can be reused
        raise


def _run(name: str, folder: Path, trial: Config, overrides: dict, llm_client, max_minutes: float, echo,
         http_transport=None) -> dict:
    rt = build_runtime(trial, llm_client=llm_client, http_transport=http_transport)
    ctx = rt.offices.system_ctx(OFFICE)
    apply_overrides(rt, ctx, overrides)

    from .cli import import_csv  # avoid a circular import at module load

    with SAMPLES.open(newline="", encoding="utf-8-sig") as f:
        import_csv(rt, ctx, f, SAMPLES.name, "website")
    models = model_line(rt, ctx)
    echo(f"Trial '{name}' (" + ", ".join(f"{n}: {m['model']}{' @' + m['effort'] if m['effort'] else ''}"
                                         for n, m in models.items()) + ")")

    deadline = time.monotonic() + max_minutes * 60
    while time.monotonic() < deadline:
        report = rt.atlas.tick(OFFICE)
        statuses = [i.status for i in rt.board.items(ctx, limit=100)]
        echo(f"  {', '.join(sorted(statuses))}")
        if report and report.llm_unavailable:
            raise RuntimeError(f"Claude is unavailable: {report.llm_unavailable}")
        if all(st in SETTLED for st in statuses):
            break
        if report and not (report.ran or report.assigned):
            time.sleep(5)  # waiting on a retry backoff
    summary = summarise(rt, ctx, name, models)
    (folder / "summary.json").write_text(json.dumps(summary, indent=2))
    echo(f"Total ${summary['total_cost']:.4f} for {len(summary['leads'])} leads "
         f"(${summary['total_cost'] / max(1, len(summary['leads'])):.4f} per lead). Saved to {folder}")
    return summary


def summarise(rt, ctx, name: str, models: dict) -> dict:
    names = {e.id: e.name for e in rt.offices.employees(ctx, include_fired=True)}
    with rt.sessions() as s:
        costs = s.execute(select(UsageEvent.work_item_id, UsageEvent.employee_id, func.sum(UsageEvent.cost_usd),
                                 func.sum(UsageEvent.input_tokens), func.sum(UsageEvent.output_tokens))
                          .where(UsageEvent.org_id == ctx.org_id)
                          .group_by(UsageEvent.work_item_id, UsageEvent.employee_id)).all()
    per_item: dict[str, dict] = {}
    for item_id, emp_id, cost, tin, tout in costs:
        per_item.setdefault(item_id, {})[names.get(emp_id, "?")] = {
            "cost": round(cost or 0, 5), "input_tokens": tin, "output_tokens": tout}
    out = []
    for item in rt.board.items(ctx, limit=100):
        folder = rt.files.item_dir(ctx.org_id, item.id, create=False)
        copy_path, qa_path = folder / "copy.json", folder / "qa" / "qa_report.json"
        shot = folder / "qa" / "screens" / "1440.png"
        out.append({
            "business": item.business_name,
            "status": item.status,
            "designer": names.get(item.assigned_employee_id or ""),
            "fix_count": item.fix_count,
            "cost": round(sum(a["cost"] for a in per_item.get(item.id, {}).values()), 5),
            "by_agent": per_item.get(item.id, {}),
            "copy": json.loads(copy_path.read_text()) if copy_path.exists() else None,
            "qa": json.loads(qa_path.read_text()) if qa_path.exists() else None,
            # Relative to compare.html, which sits in the trials folder next to each trial's folder
            "screenshot": f"{name}/{shot.relative_to(rt.config.settings.data_path).as_posix()}" if shot.exists() else None,
        })
    out.sort(key=lambda l: l["business"])
    return {"name": name, "models": models, "leads": out, "total_cost": round(sum(l["cost"] for l in out), 5)}


def compare(names: list[str], config: Config | None = None) -> Path:
    """Write data/trials/compare.html: each business in a row, one column per trial."""
    root = trials_root(config or get_config())
    trials = []
    for name in names:
        path = root / name / "summary.json"
        if not path.exists():
            raise ValueError(f"No finished trial called '{name}' (looked for {path})")
        trials.append(json.loads(path.read_text()))

    e = html.escape
    head = "".join(
        f"<th>{e(t['name'])}<div class='muted'>{e(', '.join(f'{a}: {m['model']}' + (f' @{m['effort']}' if m['effort'] else '') for a, m in t['models'].items()))}</div>"
        f"<div class='cost'>${t['total_cost']:.4f} total · ${t['total_cost'] / max(1, len(t['leads'])):.4f}/lead</div></th>"
        for t in trials)
    rows = []
    for business in [l["business"] for l in trials[0]["leads"]]:
        cells = []
        for t in trials:
            lead = next((l for l in t["leads"] if l["business"] == business), None)
            if not lead:
                cells.append("<td class='muted'>not in this trial</td>")
                continue
            copy = lead["copy"] or {}
            qa = lead["qa"] or {}
            scores = ", ".join(f"{k} {v}" for k, v in (qa.get("lighthouse", {}).get("scores") or {}).items())
            services = "".join(f"<li><b>{e(s['name'])}</b>: {e(s['description'])}</li>" for s in copy.get("services", []))
            assumed = f"<p class='warn'>Assumed: {e('; '.join(copy['assumptions']))}</p>" if copy.get("assumptions") else ""
            shot = f"<a href='{e(lead['screenshot'])}'><img src='{e(lead['screenshot'])}' alt='{e(business)} at 1440px'></a>" if lead["screenshot"] else ""
            by_agent = ", ".join(f"{a} ${v['cost']:.4f} ({v['output_tokens']} out)" for a, v in lead["by_agent"].items())
            cells.append(
                f"<td><div class='cost'>${lead['cost']:.4f}</div><div class='muted'>{e(by_agent)}</div>"
                f"<p class='status'>{e(lead['status'])} · {e(lead['designer'] or '')}{' · fixes ' + str(lead['fix_count']) if lead['fix_count'] else ''}"
                f"{' · QA passed' if qa.get('passed') else ''}{' · ' + e(scores) if scores else ''}</p>"
                f"<h3>{e(copy.get('headline', ''))}</h3><p><i>{e(copy.get('subheadline', ''))}</i></p>"
                f"<p>{e(copy.get('about', ''))}</p><ul>{services}</ul><p><b>CTA:</b> {e(copy.get('cta', ''))}</p>{assumed}{shot}</td>")
        rows.append(f"<tr><th class='biz'>{e(business)}</th>{''.join(cells)}</tr>")

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Wots Office · model comparison</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;color:#1c1917;background:#faf7f0}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #d6d3d1;padding:12px;vertical-align:top;text-align:left;background:#fff}}
thead th{{background:#f3ead3;position:sticky;top:0}} th.biz{{width:140px;background:#f3ead3}}
.muted{{color:#78716c;font-size:13px;font-weight:normal}} .cost{{font-size:20px;font-weight:700}}
.warn{{background:#fef3c7;padding:6px}} img{{width:100%;max-height:320px;object-fit:cover;object-position:top;border:1px solid #d6d3d1}}
h3{{margin:8px 0 4px}} .status{{font-size:13px;color:#57534e}}
</style></head><body><h1>Model comparison</h1>
<p class="muted">Same 4 sample leads, same pipeline; only the model and effort settings differ. Costs are the real billed usage.</p>
<table><thead><tr><th></th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></body></html>"""
    out = root / "compare.html"
    out.write_text(page)
    return out
