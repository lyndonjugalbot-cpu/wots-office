"""The Phase 1 employee implementations' own logic (no browser, no real LLM)."""
import json
from types import SimpleNamespace

import pytest

from wots.core.metering import LLMError
from wots.employees.base import EmployeeContext, EmployeeInfo
from wots.employees.impl.copywriter import Copywriter
from wots.employees.impl.data_verifier import DataVerifier, format_phone, region_timezone
from wots.employees.impl.graphic_designer import GraphicDesigner, contrast, family_for
from wots.employees.impl.lead_researcher import LeadResearcher
from wots.employees.impl.web_developer import STYLE_FILES, WebDeveloper

from .conftest import internal, new_item


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


def test_every_web_developer_style_has_a_stylesheet(make_runtime):
    rt = make_runtime()
    styles = rt.catalogue.types["web_developer"].config_schema["style_profile"]["values"]
    assert set(styles) == set(STYLE_FILES)


def hire(rt, type_key, name="Test", **config):
    type_def = rt.catalogue.types[type_key]
    return EmployeeInfo(id=f"emp-{name}", name=name, type=type_def, config=type_def.validate_config(config))


def ctx_for(rt, info, item_id, llm=None, model=None, feedback=None, task=None):
    office = internal(rt)
    return EmployeeContext(org=office, employee=info, config=rt.config, item_dir=rt.files.item_dir(office.org_id, item_id),
                           task=task or info.type.task_kinds[0], dry_run=True, llm=llm, model=model, feedback=feedback,
                           tools=rt.integrations.for_office(office))


def item(rt, **fields):
    return rt.board.get(internal(rt), new_item(rt, **fields))


def test_lead_researcher_passes_imported_leads_on(make_runtime):
    rt = make_runtime(fakes=False)
    lead = item(rt, source="csv:x.csv")
    info = hire(rt, "lead_researcher")
    result = LeadResearcher(info).run(lead, "research", ctx_for(rt, info, lead.id))
    assert result.next_status == "VERIFY" and "csv:x.csv" in result.note


def test_data_verifier_enriches(make_runtime):
    rt = make_runtime(fakes=False)
    lead = item(rt, phone="503-555-0147", region="OR", email=" Hello@Biz.TEST ")
    info = hire(rt, "data_verifier")
    result = DataVerifier(info).run(lead, "verify", ctx_for(rt, info, lead.id))
    assert result.next_status == "ENRICHED"
    assert result.updates["phone"] == "(503) 555-0147" and result.updates["timezone"] == "America/Los_Angeles"
    assert result.updates["email"] == "hello@biz.test"


def test_graphic_designer_palette_always_passes_aa(make_runtime):
    rt = make_runtime(fakes=False)
    info = hire(rt, "graphic_designer", style_profile="bold_playful")
    designer = GraphicDesigner(info)
    for name in ["A", "Bright Yellow Co", "Harbour Line Plumbing", "Salt & Sage Day Spa", "Zed"]:
        lead = item(rt, name=name, category="Florist")
        ctx = ctx_for(rt, info, lead.id, task="website_assets")
        designer.run(lead, "website_assets", ctx)
        brand = json.loads((ctx.item_dir / "assets" / "brand.json").read_text())
        assert contrast(brand["primary"], "#ffffff") >= 4.5
    with pytest.raises(NotImplementedError):
        designer.run(lead, "ad_variants", ctx)  # the ad workflow's task arrives in Phase 2


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


def test_copywriter_writes_copy_and_rejects_empty_answers(make_runtime):
    rt = make_runtime(fakes=False)
    lead = item(rt, category="Plumber")
    info = hire(rt, "copywriter", name="Quill", effort="low")
    llm = Recorder(COPY)
    ctx = ctx_for(rt, info, lead.id, llm, "claude-opus-5")
    ctx.effort = info.config["effort"]
    result = Copywriter(info).run(lead, "write_copy", ctx)
    assert result.next_status == "COPY_READY" and "assumed: Leak repairs" in result.note
    system = llm.calls[0]["system"]
    assert "en-US" in system and "Never invent facts" in system and "Quill" in system
    assert llm.calls[0]["effort"] == "low"
    with pytest.raises(LLMError):
        Copywriter(info).run(lead, "write_copy", ctx_for(rt, info, lead.id, Recorder({**COPY, "headline": ""}), "m"))


def test_web_developer_revises_only_for_ceo_notes_or_proofread(make_runtime):
    assert WebDeveloper._needs_revision("CEO notes: mention Torquay")
    assert WebDeveloper._needs_revision("QA report: [medium] Proofread: 'color' should be 'colour'")
    assert not WebDeveloper._needs_revision("QA report: [high] Placeholder text found: “todo”; [high] The phone on the page doesn't match the lead record (x)")

    rt = make_runtime(fakes=False)
    lead = item(rt, category="Plumber", phone="(503) 555-0147", email="a@b.test", address="1 Main St")
    info = hire(rt, "web_developer", name="Pixel", style_profile="clean_modern")
    ctx = ctx_for(rt, info, lead.id)
    (ctx.item_dir / "copy.json").write_text(json.dumps({**COPY, "locale": "en-US"}))
    pixel = WebDeveloper(info)

    llm = Recorder({"copy": {**COPY, "headline": "Portland's local plumbers"}, "primary_color": "#ffff00",
                    "template": "", "changes": "Mentioned Portland"})
    result = pixel.run(lead, "fix_website", ctx_for(rt, info, lead.id, llm, "claude-opus-5",
                                                    feedback="CEO notes: mention Portland"))
    html = (ctx.item_dir / "site" / "index.html").read_text()
    assert "Portland&#39;s local plumbers" in html and "revised: Mentioned Portland" in result.note
    assert "--primary: #ffff00" not in html  # a too-light colour is darkened to keep AA contrast
    assert 'content="noindex' in html
    assert [a.kind for a in result.artifacts] == ["site", "copy"]

    quiet = Recorder({})
    pixel.run(lead, "fix_website", ctx_for(rt, info, lead.id, quiet, "claude-opus-5",
                                           feedback="QA report: [high] Placeholder text found: “todo”"))
    assert quiet.calls == []  # mechanical fixes are a clean rebuild, no LLM spend


@pytest.mark.parametrize("style", sorted(STYLE_FILES))
def test_each_style_profile_builds(make_runtime, style):
    rt = make_runtime(fakes=False)
    lead = item(rt, category="Café", phone="(503) 555-0147")
    info = hire(rt, "web_developer", name="Nova", style_profile=style)
    ctx = ctx_for(rt, info, lead.id)
    (ctx.item_dir / "copy.json").write_text(json.dumps({**COPY, "locale": "en-US"}))
    WebDeveloper(info).run(lead, "build_website", ctx)
    assert (ctx.item_dir / "site" / "index.html").is_file()
