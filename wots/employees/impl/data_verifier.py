"""Data Verifier (Ledger in the internal office): verifies and enriches leads (spec v2 §8, §11).

Cheapest checks first; the first failure disqualifies the lead with a reason:
  1. country: one we work in (US, UK, AU)
  2. dedupe: not the same business as an earlier lead in this office
  3. no real website: a website from Google counts unless it's only a social profile; then we try
     likely domains (joescafe.co.uk...) and look for the business's name or phone on the page
  4. registry: UK -> Companies House (sole traders and partnerships aren't on it, and UK rules don't
     let us cold-email them); AU -> ABN Lookup (cancelled ABNs are dropped)
  5. country rules: the entity type must be contactable
Then enrichment: timezone, phone in the country's format, tidy email, postcode. Everything found,
and where from, is kept in the lead's `checks` so the CEO can see why.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ...core.lead_identity import extract_postcode, normalise_postcode, similarity
from ...integrations.errors import IntegrationConfigError
from ...integrations.web_presence import check_domain, likely_domains, social_kind
from ..base import BaseEmployee, EmployeeContext, EmployeeResult

CH_TYPES = {"ltd": "ltd", "private-limited-guarant-nsc": "ltd", "private-limited-guarant-nsc-limited-exemption": "ltd",
            "private-limited-shares-section-30-exemption": "ltd", "plc": "plc", "llp": "llp"}
ABN_MIN_SCORE = 90

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


class DataVerifier(BaseEmployee):
    type_key = "data_verifier"

    def run(self, lead, task: str, ctx: EmployeeContext) -> EmployeeResult:
        rules = ctx.config.countries.get(lead.country)
        if not rules:
            return self._drop("country_not_targeted", f"Country {lead.country} isn't one we work in", {})
        if ctx.tools is None:
            raise IntegrationConfigError("Verification needs the office's integrations")
        checks: dict = {"checked_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")}
        postcode = normalise_postcode(lead.postcode, lead.country) or extract_postcode(lead.address, lead.country)
        lead.postcode = postcode

        dupes = ctx.tools.duplicates(lead)
        checks["dedupe"] = dupes
        if dupes:
            d = dupes[0]
            return self._drop("duplicate", f"Same business as {d['business_name']} ({d['match']})", checks)

        social, reason = self._website(lead, ctx, checks)
        if reason:
            return self._drop("has_website", reason, checks)

        registry = {}
        if lead.country == "UK" and lead.source == "companies_house" and lead.registry_id:
            # Found by trade on Companies House as an active company: already confirmed
            registry, reason = {"entity_type": lead.entity_type, "registry_id": lead.registry_id}, None
            checks["registry"] = {"source": "companies_house", "candidates": [],
                                  "match": {"number": lead.registry_id, "type": lead.entity_type, "status": "active"}}
        elif lead.country == "UK":
            registry, reason = self._companies_house(lead, postcode, ctx, checks)
        elif lead.country == "AU":
            registry, reason = self._abn(lead, postcode, ctx, checks)
        if reason:
            return self._drop(*reason, checks)

        entity = registry.get("entity_type") or lead.entity_type or "unknown"
        allowed = rules.contactable_entity_types
        if "any" not in allowed and entity not in allowed:
            return self._drop("entity_not_contactable", f"{entity} isn't contactable under {lead.country} rules", checks)

        updates = {
            "timezone": lead.timezone or region_timezone(lead.country, lead.region),
            "phone": format_phone(lead.phone, lead.country, rules.phone_format),
            "email": lead.email.strip().lower() if lead.email else lead.email,
            "postcode": postcode,
            "entity_type": entity,
            "social_links": {**(lead.social_links or {}), **social},
            "checks": checks,
            **{k: v for k, v in registry.items() if k == "registry_id"},
        }
        if lead.website_found and social_kind(lead.website_found):
            updates["website_found"] = None  # a social profile, now in social_links
        found = [f"{entity}" + (f" {registry['registry_id']}" if registry.get("registry_id") else "")]
        missing = [f for f in ("phone", "email", "address") if not getattr(lead, f)]
        note = ("Verified: no website, " + ", ".join(found) + (f"; missing {', '.join(missing)}" if missing else ""))
        return EmployeeResult("ENRICHED", note, updates=updates)

    @staticmethod
    def _drop(reason: str, note: str, checks: dict) -> EmployeeResult:
        return EmployeeResult("DISQUALIFIED", f"Disqualified ({reason}): {note}",
                              updates={"disqualify_reason": reason, "checks": checks})

    @staticmethod
    def _website(lead, ctx: EmployeeContext, checks: dict) -> tuple[dict, str | None]:
        """Social profiles found, and the reason to drop the lead if it already has a real website."""
        social: dict = {}
        record = {"listed": lead.website_found, "domains": []}
        checks["website"] = record
        if lead.website_found:
            kind = social_kind(lead.website_found)
            if not kind:
                return social, f"Already has a website: {lead.website_found}"
            social[kind] = lead.website_found
        for domain in likely_domains(lead.business_name, lead.country):
            result = check_domain(ctx.tools.web(), domain, lead.business_name, lead.phone)
            record["domains"].append({"domain": domain, "status": result.status, "evidence": result.evidence})
            if result.status == "match":
                return social, f"Already has a website: {domain} ({result.evidence})"
        return social, None

    @staticmethod
    def _companies_house(lead, postcode, ctx: EmployeeContext, checks: dict):
        companies = ctx.tools.companies_house().search(lead.business_name)
        best, best_score = None, 0.0
        for c in companies:
            score = similarity(lead.business_name, c.name)
            same_place = bool(postcode) and normalise_postcode(c.postcode, "UK") == postcode
            if (same_place and score >= 0.5) or score >= 0.85:
                score += 1 if same_place else 0
                if score > best_score:
                    best, best_score = c, score
        checks["registry"] = {"source": "companies_house", "candidates": [c.name for c in companies[:5]],
                              "match": best and {"number": best.number, "name": best.name, "type": best.type,
                                                 "status": best.status}}
        if best is None:
            return {}, ("uk_sole_trader_or_partnership",
                        "No Companies House match, so probably a sole trader or partnership; "
                        "UK rules need their prior consent before we email them")
        if (best.status or "").lower() != "active":
            return {}, ("company_not_active", f"{best.name} ({best.number}) is {best.status} at Companies House")
        return {"entity_type": CH_TYPES.get(best.type or "", best.type or "unknown"), "registry_id": best.number}, None

    @staticmethod
    def _abn(lead, postcode, ctx: EmployeeContext, checks: dict):
        abn = ctx.tools.abn()
        matches = [m for m in abn.match(lead.business_name, postcode) if m.score >= ABN_MIN_SCORE]
        if postcode:
            matches = [m for m in matches if not m.postcode or m.postcode == postcode] or []
        checks["registry"] = {"source": "abn_lookup", "candidates": [m.name for m in matches[:5]]}
        if not matches:
            checks["registry"]["match"] = None
            return {"entity_type": "unknown"}, None  # not every trading name is registered; keep it
        record = abn.details(max(matches, key=lambda m: m.score).abn)
        checks["registry"]["match"] = {"abn": record.abn, "status": record.status, "entity": record.entity_name,
                                       "type": record.entity_type}
        if record.status.lower() != "active":
            return {}, ("abn_cancelled", f"ABN {record.abn} ({record.entity_name}) is {record.status}")
        return {"entity_type": _au_entity(record.entity_type), "registry_id": record.abn}, None


def _au_entity(type_name: str | None) -> str:
    name = (type_name or "").lower()
    if "sole trader" in name or "individual" in name:
        return "sole_trader"
    if "partnership" in name:
        return "partnership"
    if "company" in name:
        return "company"
    if "trust" in name:
        return "trust"
    return "other" if name else "unknown"
