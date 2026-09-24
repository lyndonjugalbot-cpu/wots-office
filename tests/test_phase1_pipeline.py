"""Phase 1 acceptance (spec §12):
  4 sample leads flow to APPROVED; a deliberately broken site gets NEEDS_FIX, is fixed and
  re-passes; designers never exceed WIP 2. Also: a CEO rejection with notes is revised and re-passes.

Everything is real (Ledger, Iris, Pixel, Nova, Hawk with Playwright and Lighthouse) except
Claude, which is replaced by a scripted client so the test is free and repeatable.
Run just this with: pytest -m e2e
"""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from wots.agents.base import load_agents
from wots.agents.web_designer import WebDesigner
from wots.core import cli
from wots.core.models import Event, Lead, QAReport
from wots.core.states import ACTIVE_STATUSES
from wots.dashboard.actions import approve_build, reject_build

pytestmark = pytest.mark.e2e
SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "phase1_leads.csv"


class ScriptedClaude:
    """Answers Quill with honest copy built from the record, and Hawk's proofread with no issues."""

    def __init__(self):
        self.calls = 0
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        if kw["system"].startswith("You are Quill"):
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


class FaultyOnceDesigner(WebDesigner):
    """Ships a broken first build for one lead: a TODO left in and the wrong phone number."""

    def __init__(self, config, target: str):
        super().__init__(config)
        self.target = target
        self.broke = False

    def run(self, lead, ctx):
        result = super().run(lead, ctx)
        if lead.business_name == self.target and not self.broke:
            self.broke = True
            page = ctx.lead_dir / "site" / "index.html"
            html = page.read_text().replace(lead.phone, "555 000 0000").replace("</main>", "<p>TODO: opening hours</p></main>")
            page.write_text(html)
        return result


def active_counts(rt):
    with rt.sessions() as s:
        return {d: s.scalar(select(func.count()).select_from(Lead).where(
            Lead.assigned_to == d, Lead.status.in_([st.value for st in ACTIVE_STATUSES]))) for d in ("pixel", "nova")}


def test_four_sample_leads_reach_approved_with_one_fix_loop(make_runtime, tmp_path, monkeypatch, clock, capsys):
    if not shutil.which("node"):
        pytest.skip("Node is needed for Lighthouse")
    from wots.core.config import load_config

    config = load_config()
    agents = load_agents(config)
    assert set(agents) == {"ledger", "quill", "iris", "pixel", "nova", "hawk"}
    agents["pixel"] = FaultyOnceDesigner(config.agents.agents["pixel"], "Northgate Bookkeeping")
    agents["nova"] = FaultyOnceDesigner(config.agents.agents["nova"], "Northgate Bookkeeping")
    for agent in agents.values():  # FakeAgent-style test agents are keyed by name
        agent.name = agent.config.name

    rt = make_runtime(list(agents.values()), qa={"check_external_links": False})
    claude = ScriptedClaude()
    rt.llm._client = claude

    # Import the sample CSV exactly as `wots import-leads` does
    assert cli.cmd_import_leads(rt, SimpleNamespace(csv=str(SAMPLES), scope="website")) == 0
    assert "Imported 4 lead(s)" in capsys.readouterr().out

    saw_needs_fix = rejected = False
    for _ in range(20):
        rt.atlas.tick()
        assert all(n <= 2 for n in active_counts(rt).values()), active_counts(rt)
        with rt.sessions() as s:
            statuses = dict(s.execute(select(Lead.business_name, Lead.status)).all())
            waiting = list(s.scalars(select(Lead.id).where(Lead.status == "READY_FOR_APPROVAL")))
            saw_needs_fix |= bool(s.scalar(select(func.count()).select_from(Event).where(Event.to_status == "NEEDS_FIX")))
        for lead_id in waiting:  # the CEO approves what Hawk passed, but sends one back with notes first
            if rt.board.get(lead_id).business_name == "Harbour Line Plumbing" and not rejected:
                reject_build(rt.board, lead_id, "Please mention Portland in the headline")
                rejected = True
            else:
                approve_build(rt.board, lead_id)
        if all(v == "APPROVED" for v in statuses.values()) and len(statuses) == 4:
            break
        clock.advance(seconds=30)

    with rt.sessions() as s:
        final = dict(s.execute(select(Lead.business_name, Lead.status)).all())
        broken = s.scalars(select(Lead).where(Lead.business_name == "Northgate Bookkeeping")).one()
        reports = list(s.scalars(select(QAReport).where(QAReport.lead_id == broken.id).order_by(QAReport.id)))
        path = list(s.scalars(select(Event.to_status).where(Event.lead_id == broken.id).order_by(Event.id)))

    assert set(final.values()) == {"APPROVED"}, final
    assert saw_needs_fix
    # The broken build failed QA for the right reasons, went back to the same designer, and re-passed
    assert [r.passed for r in reports] == [False, True]
    problems = " ".join(i["description"] for i in reports[0].issues)
    assert "todo" in problems.lower() and "phone" in problems.lower()
    assert path[path.index("NEEDS_FIX"):path.index("NEEDS_FIX") + 4] == ["NEEDS_FIX", "BUILDING", "IN_QA", "READY_FOR_APPROVAL"]
    assert broken.fix_count == 1

    # Every approved site kept its noindex tag, the lead's real details and screenshots at all 3 widths
    lead_root = rt.config.settings.data_path / "leads"
    for lead_dir in lead_root.iterdir():
        html = (lead_dir / "site" / "index.html").read_text()
        assert 'content="noindex' in html
        assert {p.name for p in (lead_dir / "qa" / "screens").iterdir()} == {"375.png", "768.png", "1440.png"}
        report = json.loads((lead_dir / "qa" / "qa_report.json").read_text())
        scores = report["lighthouse"]["scores"]
        assert scores["accessibility"] >= 90 and scores["performance"] >= 80 and scores["seo"] >= 80, scores
    # The CEO's notes reached the designer, who revised the copy with Claude and rebuilt
    harbour = next(d for d in lead_root.iterdir() if "Harbour Line" in (d / "site" / "index.html").read_text())
    assert "(revised)" in (harbour / "site" / "index.html").read_text()
    assert claude.calls >= 10  # Quill x4, designer revision x1, Hawk proofread x6
