"""Pixel / Nova: web designers (spec §7). One class; the style profile in config/agents.yaml
decides the look.

The site is built from templates/sites/ (one base plus a template per business category),
filled with Quill's copy, Iris's brand and logo, and the lead's contact details exactly as
recorded, so Hawk's "details match the lead" check can pass. Output: data/leads/{id}/site/.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from urllib.parse import quote_plus

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

from ..core.config import ROOT
from ..core.llm import LLMError
from ..core.models import Lead
from .base import AgentContext, AgentResult, ArtifactOut, BaseAgent
from .graphic_designer import KEYWORDS, darken_until, family_for
from .quill import COPY_SCHEMA

TEMPLATES = ROOT / "templates" / "sites"
STYLE_FILES = {"clean & modern": "clean-modern.css", "bold & warm": "bold-warm.css"}
# Simple line icons per business family (inline SVG, decorative)
ICONS = {
    "trades": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-2.5z"/></svg>',
    "food": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 3v8a3 3 0 0 0 6 0V3M7 3v18M17 3c-2 2-3 5-3 8h3v10"/></svg>',
    "beauty": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5z"/></svg>',
    "retail": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 8h14l-1 13H6zM9 8V6a3 3 0 0 1 6 0v2"/></svg>',
    "professional": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16v12H4zM9 7V5h6v2M4 12h16"/></svg>',
}

FAMILIES = ["professional", *[f for f in KEYWORDS if f != "professional"]]
REVISION_SCHEMA = {
    "type": "object",
    "properties": {
        "copy": COPY_SCHEMA,
        "primary_color": {"type": "string", "description": "New brand colour as #rrggbb, or empty to keep"},
        "template": {"type": "string", "enum": ["", *FAMILIES], "description": "Different template, or empty to keep"},
        "changes": {"type": "string", "description": "One sentence: what you changed"},
    },
    "required": ["copy", "primary_color", "template", "changes"],
    "additionalProperties": False,
}
# Feedback that only a rebuild can fix (Hawk's mechanical checks) doesn't need an LLM revision
MECHANICAL = re.compile(r"placeholder|doesn't match the lead|noindex|console error|image doesn't load|broken link|"
                        r"missing section|lighthouse|scrolls sideways|site/index.html", re.IGNORECASE)

_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]),
                   undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)


class WebDesigner(BaseAgent):
    def run(self, lead: Lead, ctx: AgentContext) -> AgentResult:
        copy = json.loads((ctx.lead_dir / "copy.json").read_text())
        brand_path = ctx.lead_dir / "assets" / "brand.json"
        brand = json.loads(brand_path.read_text()) if brand_path.exists() else {
            "family": family_for(lead.category), "primary": "#1e3a8a", "primary_dark": "#172554",
            "accent": "#f59e0b", "ink": "#1c1917", "paper": "#fffdf8"}
        style_file = STYLE_FILES.get((self.config.style_profile or "").lower(), "clean-modern.css")
        revised = ""
        if ctx.feedback and self._needs_revision(ctx.feedback):
            copy, brand, revised = self._revise(lead, ctx, copy, brand)

        site = ctx.lead_dir / "site"
        if site.exists():
            shutil.rmtree(site)  # a rebuild (e.g. after QA feedback) starts clean
        (site / "assets").mkdir(parents=True)
        logo = ctx.lead_dir / "assets" / "logo.svg"
        if logo.exists():
            shutil.copy(logo, site / "assets" / "logo.svg")
        logo_width = int(re.search(r'width="(\d+)"', logo.read_text()).group(1)) if logo.exists() else 200

        html = _env.get_template(f"{brand['family']}.html").render(
            lead=lead,
            copy=copy,
            brand=brand,
            locale=copy.get("locale", "en"),
            style_css=Markup((TEMPLATES / "styles" / style_file).read_text()),
            icon=Markup(ICONS[brand["family"]]),
            phone_href=re.sub(r"[^\d+]", "", lead.phone or ""),
            map_url=f"https://www.google.com/maps/search/?api=1&query={quote_plus(lead.address or '')}",
            logo_width=logo_width,
            gallery=[],  # only real photos may go here (Phase 3); none exist yet
            year=date.today().year,
        )
        (site / "index.html").write_text(html)
        note = f"Built with the {brand['family']} template, {self.config.style_profile} style"
        artifacts = [ArtifactOut("site", "site/index.html")]
        if revised:
            note += f"; revised: {revised}"
            artifacts.append(ArtifactOut("copy", "copy.json"))
        elif ctx.feedback:
            note += "; clean rebuild for the QA findings"
        return AgentResult("IN_QA", note, artifacts=artifacts)

    @staticmethod
    def _needs_revision(feedback: str) -> bool:
        """CEO notes and proofread findings need a real revision; mechanical QA failures just need a rebuild."""
        if feedback.startswith("CEO notes:"):
            return True
        findings = [f for f in feedback.removeprefix("QA report:").split(";") if f.strip()]
        return any(not MECHANICAL.search(f) for f in findings)

    def _revise(self, lead: Lead, ctx: AgentContext, copy: dict, brand: dict) -> tuple[dict, dict, str]:
        if not ctx.llm or not ctx.model:
            raise LLMError(f"{self.name} needs an LLM to act on this feedback")
        rules = ctx.config.countries[lead.country]
        facts = {k: getattr(lead, k) for k in ("business_name", "category", "description", "region", "country") if getattr(lead, k)}
        response = ctx.llm.complete(
            agent=self.name, model=ctx.model, lead_id=lead.id,
            system=(f"You are {lead.assigned_to or self.name}, a web designer whose style is {self.config.style_profile}. "
                    "A one-page site you built was sent back with feedback. Revise its copy and, only if the feedback "
                    "asks for it, the brand colour or template. Keep everything else the same. "
                    f"Write in {rules.spelling}. Never invent facts: no reviews, awards, years in business, prices or "
                    "guarantees beyond the business record. Keep `assumptions` accurate."),
            messages=[{"role": "user", "content":
                       f"Business record:\n{json.dumps(facts, indent=2)}\n\nCurrent copy:\n{json.dumps(copy, indent=2)}\n\n"
                       f"Current template: {brand['family']}; brand colour {brand['primary']}.\n\nFeedback:\n{ctx.feedback}"}],
            output_config={"format": {"type": "json_schema", "schema": REVISION_SCHEMA}},
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"{self.name}'s revision wasn't valid JSON") from e
        new_copy = {**data["copy"], "locale": copy.get("locale", rules.spelling)}
        if not new_copy.get("headline") or not new_copy.get("services"):
            raise LLMError(f"{self.name}'s revision dropped the headline or services")
        (ctx.lead_dir / "copy.json").write_text(json.dumps(new_copy, indent=2, ensure_ascii=False))
        brand = dict(brand)
        if re.fullmatch(r"#[0-9a-fA-F]{6}", data.get("primary_color") or ""):
            brand["primary"] = darken_until(data["primary_color"])  # still has to pass AA contrast
            brand["primary_dark"] = darken_until(brand["primary"], ratio=8)
        if data.get("template") in FAMILIES:
            brand["family"] = data["template"]
        return new_copy, brand, data.get("changes", "")[:200] or "copy revised"
