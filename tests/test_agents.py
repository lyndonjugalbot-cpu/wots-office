"""Unit tests for the Phase 1 agents' own logic (no browser, no real LLM)."""
import json
from types import SimpleNamespace

import pytest

from wots.agents.base import AgentContext
from wots.agents.graphic_designer import GraphicDesigner, contrast, family_for
from wots.agents.ledger import Ledger, format_phone, region_timezone
from wots.agents.quill import Quill
from wots.agents.web_designer import WebDesigner
from wots.core.config import load_config
from wots.core.llm import LLMError

from .conftest import new_lead


@pytest.mark.parametrize("raw,country,pattern,expected", [
    ("503-555-0147", "US", "(XXX) XXX-XXXX", "(503) 555-0147"),
    ("+1 503 555 0147", "US", "(XXX) XXX-XXXX", "(503) 555-0147"),
    ("01632 960 482", "UK", "+44 XXXX XXXXXX", "+44 1632 960482"),
    ("03 5550 1832", "AU", "+61 X XXXX XXXX", "+61 3 5550 1832"),
    ("+61 3 5550 1832", "AU", "+61 X XXXX XXXX", "+61 3 5550 1832"),
    ("12345", "US", "(XXX) XXX-XXXX", "12345"),  # doesn't fit: left as given, never guessed
])
def test_phone_formatting(raw, country, pattern, expected):
    assert format_phone(raw, country, pattern) == expected


def test_timezones_follow_region():
    assert region_timezone("AU", "VIC") == "Australia/Melbourne"
    assert region_timezone("US", "OR") == "America/Los_Angeles"
    assert region_timezone("UK", None) == "Europe/London"
    assert region_timezone("US", "??") == "America/New_York"


def test_categories_pick_the_right_template():
    assert [family_for(c) for c in ["Bookkeeping", "Book shop", "Plumber", "Café", "Day spa", None]] == [
        "professional", "retail", "trades", "food", "beauty", "professional"]


def ctx_for(rt, lead_id, llm=None, model=None, feedback=None):
    folder = rt.config.settings.data_path / "leads" / str(lead_id)
    folder.mkdir(parents=True, exist_ok=True)
    return AgentContext(config=rt.config, lead_dir=folder, dry_run=True, llm=llm, model=model, feedback=feedback)


def test_ledger_enriches(make_runtime):
    rt = make_runtime()
    lead = rt.board.get(new_lead(rt, phone="503-555-0147", region="OR", email=" Hello@Biz.TEST "))
    result = Ledger(load_config().agents.agents["ledger"]).run(lead, ctx_for(rt, lead.id))
    assert result.next_status == "ENRICHED"
    assert result.updates["phone"] == "(503) 555-0147" and result.updates["timezone"] == "America/Los_Angeles"
    assert result.updates["email"] == "hello@biz.test"


def test_iris_palette_always_passes_aa(make_runtime):
    rt = make_runtime()
    iris = GraphicDesigner(load_config().agents.agents["iris"])
    for name in ["A", "Bright Yellow Co", "Harbour Line Plumbing", "Salt & Sage Day Spa", "Zed"]:
        lead = rt.board.get(new_lead(rt, name=name, category="Florist"))
        iris.run(lead, ctx_for(rt, lead.id))
        brand = json.loads((ctx_for(rt, lead.id).lead_dir / "assets" / "brand.json").read_text())
        assert contrast(brand["primary"], "#ffffff") >= 4.5


class Recorder:
    def __init__(self, reply: dict):
        self.reply = reply
        self.calls = []

    def complete(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(self.reply))])


COPY = {"headline": "Plumbing you can count on", "subheadline": "Local repairs", "about": "We fix pipes.",
        "services": [{"name": "Leak repairs", "description": "Fast fixes."}], "cta": "Call us",
        "seo_title": "Harbour Line Plumbing", "meta_description": "x" * 130, "assumptions": ["Leak repairs"]}


def test_quill_writes_copy_and_rejects_empty_answers(make_runtime):
    rt = make_runtime()
    lead = rt.board.get(new_lead(rt, category="Plumber"))
    llm = Recorder(COPY)
    result = Quill(load_config().agents.agents["quill"]).run(lead, ctx_for(rt, lead.id, llm, "claude-opus-5"))
    assert result.next_status == "COPY_READY" and "assumed: Leak repairs" in result.note
    assert "en-US" in llm.calls[0]["system"] and "Never invent facts" in llm.calls[0]["system"]
    with pytest.raises(LLMError):
        Quill(load_config().agents.agents["quill"]).run(lead, ctx_for(rt, lead.id, Recorder({**COPY, "headline": ""}), "m"))


def test_designer_revises_only_for_ceo_notes_or_proofread(make_runtime):
    assert WebDesigner._needs_revision("CEO notes: mention Torquay")
    assert WebDesigner._needs_revision("QA report: [medium] Proofread: 'color' should be 'colour'")
    assert not WebDesigner._needs_revision("QA report: [high] Placeholder text found: “todo”; [high] The phone on the page doesn't match the lead record (x)")

    rt = make_runtime()
    lead_id = new_lead(rt, category="Plumber", phone="(503) 555-0147", email="a@b.test", address="1 Main St")
    lead = rt.board.get(lead_id)
    ctx = ctx_for(rt, lead_id)
    (ctx.lead_dir / "copy.json").write_text(json.dumps({**COPY, "locale": "en-US"}))
    pixel = WebDesigner(load_config().agents.agents["pixel"])

    llm = Recorder({"copy": {**COPY, "headline": "Portland's local plumbers"}, "primary_color": "#ffff00",
                    "template": "", "changes": "Mentioned Portland"})
    result = pixel.run(lead, ctx_for(rt, lead_id, llm, "claude-opus-5", feedback="CEO notes: mention Portland"))
    html = (ctx.lead_dir / "site" / "index.html").read_text()
    assert "Portland&#39;s local plumbers" in html and "revised: Mentioned Portland" in result.note
    assert "--primary: #ffff00" not in html  # a too-light colour is darkened to keep AA contrast
    assert [a.kind for a in result.artifacts] == ["site", "copy"]

    quiet = Recorder({})
    pixel.run(lead, ctx_for(rt, lead_id, quiet, "claude-opus-5", feedback="QA report: [high] Placeholder text found: “todo”"))
    assert quiet.calls == []  # mechanical fixes are a clean rebuild, no LLM spend
