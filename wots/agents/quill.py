"""Quill: writes all website copy (spec §7) as copy.json, in the lead's local spelling.

Quill must not invent facts. Anything it had to assume (such as typical services for the
category) is listed in `assumptions`, which the CEO sees in the approval queue.
"""
from __future__ import annotations

import json

from ..core.llm import LLMError
from ..core.models import Lead
from .base import AgentContext, AgentResult, ArtifactOut, BaseAgent

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "subheadline": {"type": "string"},
        "about": {"type": "string"},
        "services": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                "required": ["name", "description"],
                "additionalProperties": False,
            },
        },
        "cta": {"type": "string"},
        "seo_title": {"type": "string"},
        "meta_description": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "subheadline", "about", "services", "cta", "seo_title", "meta_description", "assumptions"],
    "additionalProperties": False,
}

SYSTEM = """You are Quill, the copywriter at a small studio that builds websites for local businesses.
Write the copy for a one-page website for the business described by the user.

Rules:
- Write in {spelling} spelling and conventions. Use {currency} if you mention prices (normally you shouldn't).
- Never invent facts: no reviews, testimonials, ratings, awards, certifications, years in business,
  staff names, prices, guarantees, opening hours or service areas unless they appear in the record.
  If something is missing, write around it.
- Services: use services from the record. If it lists none, offer 3 to 4 plain, typical services for
  the category, phrased modestly, and name each one you assumed in `assumptions`.
- Keep it short and warm. headline under 60 characters, subheadline under 120, about 2 to 4 sentences,
  each service description one sentence, cta 2 to 5 words, seo_title under 60 characters (include the
  business name), meta_description 120 to 155 characters.
- Plain text only: no markdown, no emoji, no placeholders like [Business Name] or TODO."""


class Quill(BaseAgent):
    def run(self, lead: Lead, ctx: AgentContext) -> AgentResult:
        if not ctx.llm or not ctx.model:
            raise LLMError("Quill needs an LLM and a model")
        rules = ctx.config.countries[lead.country]
        record = {
            "business_name": lead.business_name,
            "category": lead.category,
            "description": lead.description,
            "region": lead.region,
            "country": lead.country,
            "contact_name": lead.contact_name,
        }
        prompt = "Business record (the only facts you may use):\n" + json.dumps(
            {k: v for k, v in record.items() if v}, indent=2)
        if ctx.feedback:
            prompt += f"\n\nThe previous copy was sent back. Fix this:\n{ctx.feedback}"

        response = ctx.llm.complete(
            agent=self.name, model=ctx.model, lead_id=lead.id,
            system=SYSTEM.format(spelling=rules.spelling, currency=rules.currency),
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": COPY_SCHEMA}},
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            copy = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("Quill's reply wasn't valid JSON") from e
        empty = [k for k in ("headline", "about", "cta", "seo_title", "meta_description") if not str(copy.get(k, "")).strip()]
        if empty or not copy.get("services"):
            raise LLMError(f"Quill left out: {', '.join(empty or ['services'])}")

        copy["locale"] = rules.spelling
        (ctx.lead_dir / "copy.json").write_text(json.dumps(copy, indent=2, ensure_ascii=False))
        note = "Copy written" + (f"; assumed: {'; '.join(copy['assumptions'])}" if copy["assumptions"] else "")
        return AgentResult("COPY_READY", note, artifacts=[ArtifactOut("copy", "copy.json")])
