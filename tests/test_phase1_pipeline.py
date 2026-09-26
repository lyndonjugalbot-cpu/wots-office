"""Phase 1 acceptance (spec v1 §12, kept in v2 §16):
  4 sample leads flow to APPROVED (and on to a dry-run preview, Phase 3); a deliberately broken site gets NEEDS_FIX, is fixed and
  re-passes; developers never exceed WIP 2. Also: a CEO rejection with notes is revised and re-passes.

Everything is real (the migrated internal office's Scout, Ledger, Quill, Iris/Juno, Pixel, Nova and
Hawk with Playwright and Lighthouse) except Claude, which is replaced by a scripted client so the
test is free and repeatable. Run just this with: pytest -m e2e
"""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from wots.core import cli
from wots.core.models import Event, QAReport, WorkItem
from wots.dashboard.actions import approve_build, reject_build
from wots.employees.impl.web_developer import WebDeveloper

from .conftest import as_user, employee_id, internal

pytestmark = pytest.mark.e2e
SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "phase1_leads.csv"
ACTIVE = ["ASSIGNED", "BUILDING", "IN_QA", "NEEDS_FIX", "READY_FOR_APPROVAL", "ESCALATED"]


class ScriptedClaude:
    """Answers Quill with honest copy built from the record, and Hawk's proofread with no issues."""

    def __init__(self):
        self.calls = 0
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        if "the copywriter" in kw["system"]:
            record = json.loads(kw["messages"][0]["content"].split("\n", 1)[1])
            name = record["business_name"]
            body = {
                "headline": f"{name}",
                "subheadline": record.get("description", "A local business you can rely on."),
                "about": f"{name} is a local {record.get('category', 'business').lower()}. Get in touch to find out more.",
                "services": [{"name": "Friendly service", "description": "Talk to us about what you need."},
                             {"name": "Local and easy to reach", "description": "Call, email or visit us."}],
                "cta": "Get in touch",
                "seo_title": f"{name}"[:60],
                "meta_description": (f"{name}: {record.get('description', '')} Contact us today to find out how we can help you.")[:155],
                "assumptions": [],
            }
        elif "a web designer" in kw["system"]:  # a designer revising after feedback: keep the copy, report it
            current = json.loads(kw["messages"][0]["content"].split("Current copy:\n", 1)[1].split("\n\nCurrent template", 1)[0])
            current.pop("locale", None)
            body = {"copy": {**current, "headline": current["headline"] + " (revised)"}, "primary_color": "",
                    "template": "", "changes": "Tightened the headline"}
        else:
            body = {"issues": []}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(body))],
                               usage=SimpleNamespace(input_tokens=500, output_tokens=300), stop_reason="end_turn")


class FaultyOnceDeveloper(WebDeveloper):
    """Ships a broken first build for one lead: a TODO left in and the wrong phone number."""

    def __init__(self, info, target: str):
        super().__init__(info)
        self.target = target
        self.broke = False

    def run(self, lead, task, ctx):
        result = super().run(lead, task, ctx)
        if lead.business_name == self.target and not self.broke:
            self.broke = True
            page = ctx.item_dir / "site" / "index.html"
            html = page.read_text().replace(lead.phone, "555 000 0000").replace("</main>", "<p>TODO: opening hours</p></main>")
            page.write_text(html)
        return result


def active_counts(rt, ctx):
    with rt.sessions() as s:
        return {name: s.scalar(select(func.count()).select_from(WorkItem).where(
            WorkItem.org_id == ctx.org_id, WorkItem.assigned_employee_id == employee_id(rt, ctx, name),
            WorkItem.status.in_(ACTIVE))) for name in ("Pixel", "Nova")}


def test_four_sample_leads_reach_approved_with_one_fix_loop(make_runtime, clock, capsys):
    if not shutil.which("node"):
        pytest.skip("Node is needed for Lighthouse")
    claude = ScriptedClaude()
    rt = make_runtime(fakes=False, llm_client=claude, qa={"check_external_links": False})
    ctx = internal(rt)
    staff = {st.info.name: st for st in rt.atlas.staff(ctx)}
    for name in ("Pixel", "Nova"):
        rt.atlas.impl_overrides[staff[name].info.id] = FaultyOnceDeveloper(staff[name].info, "Northgate Bookkeeping")
    ceo = as_user(rt, ctx)

    # Import the sample CSV exactly as `wots import-leads` does
    assert cli.cmd_import_leads(rt, SimpleNamespace(csv=str(SAMPLES), org="wots-office", workflow="website")) == 0
    assert "Imported 4 lead(s)" in capsys.readouterr().out

    saw_needs_fix = rejected = False
    for _ in range(20):
        rt.atlas.tick()
        assert all(n <= 2 for n in active_counts(rt, ctx).values()), active_counts(rt, ctx)
        items = rt.board.items(ctx)
        with rt.sessions() as s:
            saw_needs_fix |= bool(s.scalar(select(func.count()).select_from(Event).where(
                Event.org_id == ctx.org_id, Event.to_status == "NEEDS_FIX")))
        for item in [i for i in items if i.status == "READY_FOR_APPROVAL"]:
            # The CEO approves what Hawk passed, but sends one back with notes first
            if item.business_name == "Harbour Line Plumbing" and not rejected:
                reject_build(rt.board, ceo, item.id, "Please mention Portland in the headline")
                rejected = True
            else:
                approve_build(rt.board, ceo, item.id)
        if len(items) == 4 and all(i.status == "PREVIEW_DEPLOYED" for i in rt.board.items(ctx)):
            break
        clock.advance(seconds=30)

    final = {i.business_name: i for i in rt.board.items(ctx)}
    # Approved sites went on to Dock (Phase 3), which in dry-run mode keeps the preview local
    assert {i.status for i in final.values()} == {"PREVIEW_DEPLOYED"}, {k: v.status for k, v in final.items()}
    assert all(i.checks["preview"]["live"] is False and i.preview_url is None for i in final.values())
    assert saw_needs_fix
    broken = final["Northgate Bookkeeping"]
    with rt.sessions() as s:
        reports = list(s.scalars(select(QAReport).where(QAReport.org_id == ctx.org_id, QAReport.work_item_id == broken.id)
                                 .order_by(QAReport.created_at)))
    path = [e.to_status for e in rt.board.history(ctx, broken.id)]
    # The broken build failed QA for the right reasons, went back to the same developer, and re-passed
    assert [r.passed for r in reports] == [False, True]
    problems = " ".join(i["description"] for i in reports[0].issues)
    assert "todo" in problems.lower() and "phone" in problems.lower()
    i = path.index("NEEDS_FIX")
    assert path[i:i + 4] == ["NEEDS_FIX", "BUILDING", "IN_QA", "READY_FOR_APPROVAL"]
    assert broken.fix_count == 1

    # Every approved site kept its noindex tag, the lead's real details and screenshots at all 3 widths
    for item in final.values():
        folder = rt.files.item_dir(ctx.org_id, item.id, create=False)
        html = (folder / "site" / "index.html").read_text()
        assert 'content="noindex' in html
        assert {p.name for p in (folder / "qa" / "screens").iterdir()} == {"375.png", "768.png", "1440.png"}
        report = json.loads((folder / "qa" / "qa_report.json").read_text())
        scores = report["lighthouse"]["scores"]
        assert scores["accessibility"] >= 90 and scores["performance"] >= 80 and scores["seo"] >= 80, scores
    # The CEO's notes reached the developer, who revised the copy with Claude and rebuilt
    harbour = rt.files.item_dir(ctx.org_id, final["Harbour Line Plumbing"].id, create=False)
    assert "(revised)" in (harbour / "site" / "index.html").read_text()
    assert claude.calls >= 10  # Quill x4, a developer revision x1, Hawk proofreads x6
    assert rt.meter.spend_today(ctx) > 0
