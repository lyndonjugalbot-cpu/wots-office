"""Lead research runs (spec v2 §8, Phase 2): the office's Lead Researcher searches a source for a
trade in some towns and puts new leads on the board at NEW. The workflow then verifies them.

Sources:
  osm              OpenStreetMap (free, US/UK/AU). Needs "© OpenStreetMap contributors" credit.
  companies_house  Companies House company search by trade code (free, UK only). Every hit is an
                   active limited company, which is who UK rules let us email.
  places           Google Places. OFF by default: Google's terms don't let us save business names
                   and addresses (Maps Platform ToS 3.2.3), which a lead list needs.
  scraper          our existing scraper (internal office only, behind a setting; format pending)

Every request is recorded in usage_events (with its cost, if any) and capped per office per day.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy import func, select

from ..core.context import OrgContext
from ..core.metering import start_of_local_day
from ..core.models import LeadProfile, UsageEvent
from ..integrations.existing_scraper import ExistingScraper
from ..integrations.web_presence import social_kind

COUNTRY_NAMES = {"US": "USA", "UK": "UK", "AU": "Australia"}
COUNTRY_CODES = {"US": "US", "UK": "GB", "AU": "AU"}
SOURCES = {"osm": "OpenStreetMap", "companies_house": "Companies House", "places": "Google Places",
           "scraper": "our scraper"}
USAGE_KIND = {"osm": "osm_call", "companies_house": "registry_call", "places": "places_call"}
MAX_PLACES_PAGES = 3  # Google returns at most 60 results per query
PLACES_OFF = ("Google Places is switched off: Google's terms don't allow saving business names and "
              "addresses from it (Maps Platform ToS 3.2.3). Use OpenStreetMap or Companies House.")
CH_TYPES = {"ltd": "ltd", "plc": "plc", "llp": "llp"}


class ResearchError(ValueError):
    pass


@dataclass
class Candidate:
    ref: str
    name: str
    address: str | None = None
    postcode: str | None = None
    region: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    social: dict = field(default_factory=dict)
    category: str | None = None
    status: str | None = None  # closed businesses are skipped
    country_code: str | None = None
    entity_type: str | None = None
    registry_id: str | None = None


@dataclass
class ResearchReport:
    source: str = "osm"
    created: list[str] = field(default_factory=list)
    requests: int = 0
    cost_usd: float = 0.0
    skipped: Counter = field(default_factory=Counter)  # reason -> count
    stopped: str | None = None  # why the run ended before reaching the limit

    def summary(self) -> str:
        skipped = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(self.skipped.items())) or "none"
        cost = f", ${self.cost_usd:.2f}" if self.cost_usd else ", free"
        return (f"{len(self.created)} new lead(s) from {self.requests} {SOURCES[self.source]} request(s){cost}. "
                f"Skipped: {skipped}." + (f" Stopped: {self.stopped}." if self.stopped else ""))


def requests_today(rt, ctx: OrgContext, source: str) -> int:
    since = start_of_local_day(rt.atlas.clock(), ctx.settings.get("timezone", rt.config.settings.timezone))
    with rt.sessions() as s:
        return s.scalar(select(func.count()).select_from(UsageEvent).where(
            UsageEvent.org_id == ctx.org_id, UsageEvent.kind == USAGE_KIND[source], UsageEvent.ts >= since)) or 0


def places_requests_today(rt, ctx: OrgContext) -> int:
    return requests_today(rt, ctx, "places")


def daily_limit(rt, source: str) -> int:
    s = rt.config.settings.research
    return {"osm": s.max_osm_requests_per_day, "places": s.max_places_requests_per_day}.get(source, 500)


def run_research(rt, ctx: OrgContext, *, country: str, trade: str, regions: list[str] | None = None, limit: int = 50,
                 source: str = "osm", workflow: str = "website") -> ResearchReport:
    country = country.upper().replace("GB", "UK")
    regions = [r.strip() for r in regions or [] if r.strip()]
    if source not in SOURCES:
        raise ResearchError(f"Unknown lead source '{source}'")
    if country not in COUNTRY_NAMES:
        raise ResearchError(f"We work in US, UK and AU, not {country}")
    if not 1 <= limit <= 200:
        raise ResearchError("Ask for between 1 and 200 leads")
    spec = rt.config.trades.get(trade.strip().lower())
    if source in {"osm", "companies_house"}:
        if spec is None:
            raise ResearchError(f"Unknown trade '{trade}'. Try one of: {', '.join(sorted(rt.config.trades))}")
        if not regions:
            raise ResearchError("Name at least one town to search in")
    elif not trade.strip():
        raise ResearchError("Say what kind of business to look for, e.g. 'plumber'")
    if source == "companies_house" and country != "UK":
        raise ResearchError("Companies House only covers the UK")
    if source == "places" and not rt.config.settings.research.places_enabled:
        raise ResearchError(PLACES_OFF)
    if workflow not in {w.workflow_key for w in rt.offices.workflows(ctx) if w.active}:
        raise ResearchError(f"The {workflow} workflow isn't active in {ctx.name}")
    researcher = next((st for st in rt.atlas.staff(ctx) if st.info.type.key == "lead_researcher"), None)
    if researcher is None:
        raise ResearchError(f"{ctx.name} has no Lead Researcher. Hire one first.")
    if country not in researcher.info.config.get("countries", [country]):
        raise ResearchError(f"{researcher.info.name} isn't set up to research {country}")
    balance = rt.meter.credit_balance(ctx)
    if balance is not None and balance <= 0:
        raise ResearchError("This office has no credits left")

    report = ResearchReport(source=source)
    if source == "scraper":
        ExistingScraper(ctx).search(country, trade, None, limit)  # internal only; waits for the format
        return report

    tools = rt.integrations.for_office(ctx)
    budget = [daily_limit(rt, source) - requests_today(rt, ctx, source)]

    def spend() -> bool:
        """Count one request against today's limit (and meter it). False once the limit is reached."""
        if budget[0] <= 0:
            report.stopped = f"today's limit of {daily_limit(rt, source)} {SOURCES[source]} requests"
            return False
        budget[0] -= 1
        cost = rt.config.settings.research.places_cost_per_request_usd if source == "places" else 0.0
        report.requests += 1
        report.cost_usd += cost
        rt.meter.record(ctx, kind=USAGE_KIND[source], cost_usd=cost, employee_id=researcher.info.id)
        return True

    batches = {"osm": _from_osm, "companies_house": _from_companies_house, "places": _from_places}[source]
    with rt.sessions() as s:
        known = set(s.scalars(select(LeadProfile.source_ref).where(
            LeadProfile.org_id == ctx.org_id, LeadProfile.source == source)))
    for where, found in batches(rt, tools, country, trade, spec, regions, spend):
        for cand in found:
            reason = _skip(cand, country, known)
            if reason:
                report.skipped[reason] += 1
                continue
            known.add(cand.ref)
            report.created.append(_create(rt, ctx, workflow, researcher.info.id, source, cand, country, where))
            if len(report.created) >= limit:
                return report
        if report.stopped:
            return report
    if len(report.created) < limit and not report.stopped:
        report.stopped = "no more results; try more towns"
    return report


# ---------------------------------------------------------------- sources (each yields (where, candidates))


def _from_osm(rt, tools, country, trade, spec, regions, spend) -> Iterator[tuple[str, list[Candidate]]]:
    osm = tools.osm()
    for town_name in regions:
        if not spend():
            return
        town = osm.find_town(town_name, country)
        if town is None:
            yield f"{town_name} (town not found)", []
            continue
        if not spend():
            return
        places = osm.search(town, spec.osm, spec.label)
        yield f"{spec.label} in {town_name}", [
            Candidate(ref=p.id, name=p.name, address=p.address, postcode=p.postcode, region=p.region, phone=p.phone,
                      email=p.email, website=p.website, social=p.social, category=p.category)
            for p in places]


def _from_companies_house(rt, tools, country, trade, spec, regions, spend):
    ch = tools.companies_house()
    size = rt.config.settings.research.companies_house_page_size
    for town in regions:
        start, total = 0, None
        while total is None or start < total:
            if not spend():
                return
            companies, total = ch.find_by_trade(spec.sic, town, size=size, start=start)
            start += size
            yield f"{spec.label} in {town}", [
                Candidate(ref=c.number, name=c.name, address=c.address, postcode=c.postcode, region=town,
                          category=spec.label, status="OPERATIONAL" if (c.status or "") == "active" else c.status,
                          entity_type=CH_TYPES.get(c.type or "", c.type), registry_id=c.number)
                for c in companies]
            if not companies:
                break


def _from_places(rt, tools, country, trade, spec, regions, spend):
    places = tools.places()
    size = rt.config.settings.research.page_size
    for region in regions or [None]:
        text = f"{trade.strip()} in {region}, {COUNTRY_NAMES[country]}" if region else \
            f"{trade.strip()} in {COUNTRY_NAMES[country]}"
        token = None
        for _ in range(MAX_PLACES_PAGES):
            if not spend():
                return
            found, token = places.search(text, country, size, token)
            yield text, [Candidate(ref=p.id, name=p.name, address=p.address, postcode=p.postcode,
                                   region=p.region(country), phone=p.phone, website=p.website, category=p.category,
                                   status=p.status, country_code=p.components.get("country")) for p in found]
            if not token:
                break


# ---------------------------------------------------------------- shared


def _skip(c: Candidate, country: str, known: set[str]) -> str | None:
    if c.ref in known:
        return "already_on_the_board"
    if c.status and c.status != "OPERATIONAL":
        return "closed"
    if c.country_code and c.country_code != COUNTRY_CODES[country]:
        return "other_country"
    if c.website and not social_kind(c.website):
        return "has_website"  # the Verifier checks the rest more deeply (likely domains, registries)
    if not c.name:
        return "no_name"
    return None


def _create(rt, ctx: OrgContext, workflow: str, researcher_id: str, source: str, c: Candidate, country: str,
            where: str) -> str:
    social = dict(c.social)
    kind = social_kind(c.website) if c.website else None
    if kind:
        social[kind] = c.website
    profile = {
        "business_name": c.name, "country": country, "category": c.category, "region": c.region, "address": c.address,
        "postcode": c.postcode, "phone": c.phone, "email": c.email, "website_found": c.website,
        "social_links": social or None, "entity_type": c.entity_type, "registry_id": c.registry_id,
        "source": source, "source_ref": c.ref,
    }
    return rt.board.create_item(ctx, workflow_key=workflow, profile={k: v for k, v in profile.items() if v},
                                actor_kind="employee", actor_id=researcher_id,
                                note=f"Found on {SOURCES[source]} ({where})")
