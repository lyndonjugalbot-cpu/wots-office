"""Web Developer (Pixel / Nova in the internal office), spec v2 §8. One class; the hire's
style_profile decides the look.

The site is built from templates/sites/ (one base plus a template per business category),
filled with Quill's copy, Iris's brand and logo, and the lead's contact details exactly as
recorded, so Hawk's "details match the lead" check can pass. Output: the item's site/ folder.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from urllib.parse import quote_plus

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

from ...core.config import ROOT
from ...core.metering import LLMError
from ..base import ArtifactOut, BaseEmployee, EmployeeContext, EmployeeResult
from .copywriter import COPY_SCHEMA
from .graphic_designer import ICONS, KEYWORDS, darken_until, family_for

TEMPLATES = ROOT / "templates" / "sites"
STYLE_FILES = {"clean_modern": "clean-modern.css", "bold_warm": "bold-warm.css", "minimal": "minimal.css",
               "playful": "playful.css"}
STYLE_NAMES = {"clean_modern": "clean & modern", "bold_warm": "bold & warm", "minimal": "minimal", "playful": "playful"}

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


class WebDeveloper(BaseEmployee):
    type_key = "web_developer"

    def run(self, lead, task: str, ctx: EmployeeContext) -> EmployeeResult:
        copy = json.loads((ctx.item_dir / "copy.json").read_text())
        brand_path = ctx.item_dir / "assets" / "brand.json"
        brand = json.loads(brand_path.read_text()) if brand_path.exists() else {
            "family": family_for(lead.category), "primary": "#1e3a8a", "primary_dark": "#172554",
            "accent": "#f59e0b", "ink": "#1c1917", "paper": "#fffdf8"}
        style = self.config.get("style_profile", "clean_modern")
        style_file = STYLE_FILES.get(style, "clean-modern.css")
        revised = ""
        if ctx.feedback and self._needs_revision(ctx.feedback):
            copy, brand, revised = self._revise(lead, ctx, copy, brand)

        site = ctx.item_dir / "site"
        if site.exists():
            shutil.rmtree(site)  # a rebuild (e.g. after QA feedback) starts clean
        (site / "assets").mkdir(parents=True)
        logo = ctx.item_dir / "assets" / "logo.svg"
        for name in ("logo.svg", "mark.svg", "hero.svg", "og.png"):  # the graphic designer's work, if any
            if (ctx.item_dir / "assets" / name).exists():
                shutil.copy(ctx.item_dir / "assets" / name, site / "assets" / name)
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
            has_mark=(site / "assets" / "mark.svg").exists(),
            hero_art=(site / "assets" / "hero.svg").exists(),
            has_og=(site / "assets" / "og.png").exists(),
            gallery=[],  # only real photos may go here (Phase 3); none exist yet
            year=date.today().year,
        )
        (site / "index.html").write_text(html)
        note = f"Built with the {brand['family']} template, {STYLE_NAMES.get(style, style)} style"
        artifacts = [ArtifactOut("site", "site/index.html")]
        if revised:
            note += f"; revised: {revised}"
            artifacts.append(ArtifactOut("copy", "copy.json"))
        elif ctx.feedback:
            note += "; clean rebuild for the QA findings"
        return EmployeeResult("IN_QA", note, artifacts=artifacts)

    @staticmethod
    def _needs_revision(feedback: str) -> bool:
        """CEO notes and proofread findings need a real revision; mechanical QA failures just need a rebuild."""
        if feedback.startswith("CEO notes:"):
            return True
        findings = [f for f in feedback.removeprefix("QA report:").split(";") if f.strip()]
        return any(not MECHANICAL.search(f) for f in findings)

    def _revise(self, lead, ctx: EmployeeContext, copy: dict, brand: dict) -> tuple[dict, dict, str]:
        if not ctx.llm or not ctx.model:
            raise LLMError(f"{self.name} needs an LLM to act on this feedback")
        rules = ctx.config.countries[lead.country]
        facts = {k: getattr(lead, k) for k in ("business_name", "category", "description", "region", "country") if getattr(lead, k)}
        response = ctx.llm.complete(
            model=ctx.model, effort=ctx.effort,
            system=(f"You are {self.name}, a web designer whose style is {STYLE_NAMES.get(self.config.get('style_profile'), 'clean & modern')}. "
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
        (ctx.item_dir / "copy.json").write_text(json.dumps(new_copy, indent=2, ensure_ascii=False))
        brand = dict(brand)
        if re.fullmatch(r"#[0-9a-fA-F]{6}", data.get("primary_color") or ""):
            brand["primary"] = darken_until(data["primary_color"])  # still has to pass AA contrast
            brand["primary_dark"] = darken_until(brand["primary"], ratio=8)
        if data.get("template") in FAMILIES:
            brand["family"] = data["template"]
        return new_copy, brand, data.get("changes", "")[:200] or "copy revised"
