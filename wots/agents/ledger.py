"""Ledger: verifies and enriches leads (spec §7).

Phase 1 is enrichment only: timezone, phone in the country's local format, tidy contact
details. Verification (no-website checks, Companies House, ABN Lookup, dedupe, country
rules) arrives in Phase 2.
"""
from __future__ import annotations

import re

from ..core.models import Lead
from .base import AgentContext, AgentResult, BaseAgent

COUNTRY_TZ = {"US": "America/New_York", "UK": "Europe/London", "AU": "Australia/Sydney"}
REGION_TZ = {
    "AU": {"NSW": "Australia/Sydney", "ACT": "Australia/Sydney", "VIC": "Australia/Melbourne",
           "QLD": "Australia/Brisbane", "SA": "Australia/Adelaide", "WA": "Australia/Perth",
           "TAS": "Australia/Hobart", "NT": "Australia/Darwin"},
    "US": {**{s: "America/New_York" for s in "CT DE FL GA MA MD ME MI NC NH NJ NY OH PA RI SC VA VT WV DC IN KY".split()},
           **{s: "America/Chicago" for s in "AL AR IA IL KS LA MN MO MS ND NE OK SD TN TX WI".split()},
           **{s: "America/Denver" for s in "CO MT NM UT WY ID".split()},
           "AZ": "America/Phoenix",
           **{s: "America/Los_Angeles" for s in "CA NV OR WA".split()},
           "AK": "America/Anchorage", "HI": "Pacific/Honolulu"},
}
DIALLING_CODE = {"US": "1", "UK": "44", "AU": "61"}


def format_phone(raw: str | None, country: str, pattern: str) -> str | None:
    """Fit the number's national digits into the country's pattern, e.g. "+44 XXXX XXXXXX".
    Numbers that don't fit are left exactly as given rather than guessed at."""
    if not raw:
        return raw
    digits = re.sub(r"\D", "", raw)
    code = DIALLING_CODE[country]
    if raw.strip().startswith("+") and digits.startswith(code):
        digits = digits[len(code):]
    elif country == "US" and len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    elif country in {"UK", "AU"} and digits.startswith("0"):
        digits = digits[1:]  # trunk prefix
    slots = pattern.count("X")
    if len(digits) != slots:
        return raw.strip()
    out, i = [], 0
    for ch in pattern:
        if ch == "X":
            out.append(digits[i])
            i += 1
        else:
            out.append(ch)
    return "".join(out)


def region_timezone(country: str, region: str | None) -> str:
    code = (region or "").strip().upper()
    return REGION_TZ.get(country, {}).get(code, COUNTRY_TZ[country])


class Ledger(BaseAgent):
    def run(self, lead: Lead, ctx: AgentContext) -> AgentResult:
        rules = ctx.config.countries.get(lead.country)
        if not rules:
            return AgentResult("DISQUALIFIED", f"Country {lead.country} is not a v1 target",
                               updates={"disqualify_reason": "country_not_targeted"})
        updates = {
            "timezone": lead.timezone or region_timezone(lead.country, lead.region),
            "phone": format_phone(lead.phone, lead.country, rules.phone_format),
            "email": lead.email.strip().lower() if lead.email else lead.email,
            "entity_type": lead.entity_type or "unknown",
            "social_links": lead.social_links or {},
        }
        missing = [f for f in ("phone", "email", "address") if not getattr(lead, f)]
        note = "Enriched (timezone, phone format)" + (f"; missing {', '.join(missing)}" if missing else "")
        return AgentResult("ENRICHED", note, updates=updates)
