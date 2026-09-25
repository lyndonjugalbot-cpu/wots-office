"""Phase 2: real leads (spec v2 §8, §11, §16). Research from Google Places, then verification:
dedupe, no-website checks, Companies House, ABN Lookup and the country rules. All against FakeWeb."""
import pytest
from sqlalchemy import func, select

from wots.core.lead_identity import extract_postcode, normalise_name, phone_digits, similarity
from wots.core.models import Integration, UsageEvent
from wots.integrations.errors import IntegrationConfigError
from wots.integrations.web_presence import likely_domains, social_kind
from wots.orchestration.research import ResearchError, run_research

from .conftest import employee_id, force_status, internal, new_item
from .fakeweb import ch_company, osm_element, place


def real(rt, *names):
    """Use the real implementations for these employees (the rest stay fakes)."""
    ctx = internal(rt)
    for name in names:
        rt.atlas.impl_overrides.pop(employee_id(rt, ctx, name))


def verify(rt, ticks=3):
    for _ in range(ticks):
        rt.atlas.tick()
    return {i.business_name: i for i in rt.board.items(internal(rt))}


# ---------------------------------------------------------------- helpers


def test_names_phones_and_postcodes_normalise():
    assert normalise_name("The Copper Kettle Café Ltd") == "copper kettle cafe"
    assert normalise_name("Joe's Plumbing & Heating LLC") == "joes plumbing heating"
    assert phone_digits("+44 161 496 0000") == phone_digits("0161 496 0000") != ""
    assert extract_postcode("12 High St, Manchester m1 1ae", "UK") == "M1 1AE"
    assert extract_postcode("1200 Main St, Portland, OR 97201-1234", "US") == "97201"
    assert extract_postcode("12 Collins St, Melbourne VIC 3000", "AU") == "3000"
    assert similarity("Northgate Bookkeeping Ltd", "NORTHGATE BOOKKEEPING LIMITED") == 1.0
    assert social_kind("https://www.facebook.com/joes") == "facebook" and social_kind("https://joes.com") is None
    assert likely_domains("The Copper Kettle Café", "UK")[:2] == ["copperkettlecafe.co.uk", "copperkettlecafe.com"]


# ---------------------------------------------------------------- research


def places_for(web, country, region, count, start=0, **kw):
    text = f"plumber in {region}, {dict(US='USA', UK='UK', AU='Australia')[country]}"
    web.places[text] = [place(f"{country}-{region}-{i}", f"{region} Plumbing {i}", country=country, **kw)
                        for i in range(start, start + count)]
    return web.places[text]


PLACES_ON = {"places_enabled": True}


def test_places_is_off_by_default(make_runtime):
    rt = make_runtime()
    with pytest.raises(ResearchError, match="Maps Platform ToS"):
        run_research(rt, internal(rt), country="US", trade="plumber", regions=["Portland"], source="places")


def test_places_research_fills_the_board_and_meters_every_request(make_runtime, web):
    rt = make_runtime(research=PLACES_ON)
    ctx = internal(rt)
    results = places_for(web, "UK", "Manchester", 45)
    results[0]["websiteUri"] = "https://manchesterplumbing0.co.uk"  # a real site: not a lead
    results[1]["websiteUri"] = "https://www.facebook.com/mp1"  # social only: still a lead
    results[2]["businessStatus"] = "CLOSED_PERMANENTLY"
    places_for(web, "UK", "Leeds", 30)
    report = run_research(rt, ctx, country="UK", trade="plumber", regions=["Manchester", "Leeds"], limit=50, source="places")
    assert len(report.created) == 50
    assert report.skipped == {"has_website": 1, "closed": 1}
    assert report.requests == 3 + 1  # Manchester: 3 pages of up to 20; Leeds: the first page was enough
    with rt.sessions() as s:
        usage = s.execute(select(func.count(), func.sum(UsageEvent.cost_usd)).where(
            UsageEvent.org_id == ctx.org_id, UsageEvent.kind == "places_call")).one()
    assert usage[0] == 4 and usage[1] == pytest.approx(4 * rt.config.settings.research.places_cost_per_request_usd)
    item = rt.board.get(ctx, report.created[0])
    assert (item.status, item.source, item.country, item.region, item.postcode) == ("NEW", "places", "UK", "Manchester", "M1 1AE")
    assert rt.board.history(ctx, item.id)[0].actor_id == employee_id(rt, ctx, "Scout")

    again = run_research(rt, ctx, country="UK", trade="plumber", regions=["Manchester"], limit=10, source="places")
    assert again.created == [] and again.skipped["already_on_the_board"] == 43


def test_the_daily_limit_stops_a_run(make_runtime, web):
    rt = make_runtime(research={**PLACES_ON, "max_places_requests_per_day": 2})
    places_for(web, "US", "Portland", 60)
    report = run_research(rt, internal(rt), country="US", trade="plumber", regions=["Portland"], limit=60, source="places")
    assert report.requests == 2 and len(report.created) == 40 and "limit" in report.stopped
    assert run_research(rt, internal(rt), country="US", trade="plumber", regions=["Portland"], limit=5, source="places").requests == 0


def test_research_refuses_what_it_shouldnt_do(make_runtime, web, monkeypatch):
    rt = make_runtime(research=PLACES_ON)
    ctx = internal(rt)
    towns = ["Portland"]
    with pytest.raises(ResearchError):
        run_research(rt, ctx, country="NZ", trade="plumber", regions=towns)
    with pytest.raises(ResearchError, match="Unknown trade"):
        run_research(rt, ctx, country="US", trade="astronaut", regions=towns)
    with pytest.raises(ResearchError, match="at least one town"):
        run_research(rt, ctx, country="US", trade="plumber")
    with pytest.raises(ResearchError, match="only covers the UK"):
        run_research(rt, ctx, country="US", trade="plumber", regions=towns, source="companies_house")
    rt.offices.update_employee(ctx, employee_id(rt, ctx, "Scout"), {"countries": ["US"]})
    with pytest.raises(ResearchError, match="isn't set up to research AU"):
        run_research(rt, ctx, country="AU", trade="plumber", regions=towns)
    monkeypatch.delenv("GOOGLE_PLACES_KEY")
    with pytest.raises(IntegrationConfigError, match="GOOGLE_PLACES_KEY"):
        run_research(rt, ctx, country="US", trade="plumber", regions=towns, source="places")
    rt.offices.fire(ctx, employee_id(rt, ctx, "Scout"))
    with pytest.raises(ResearchError, match="no Lead Researcher"):
        run_research(rt, ctx, country="US", trade="plumber", regions=towns)


def test_a_rejected_places_key_is_a_setup_problem(make_runtime, web):
    import httpx

    rt = make_runtime(research=PLACES_ON)
    original = web.handle

    def forbidden(request):
        if request.url.host == "places.googleapis.com":
            return httpx.Response(403, json={"error": {"message": "API key not valid"}})
        return original(request)

    web.transport.handler = forbidden
    with pytest.raises(IntegrationConfigError, match="refused the key"):
        run_research(rt, internal(rt), country="US", trade="plumber", regions=["Portland"], source="places")


def test_the_scraper_is_internal_only_and_behind_a_flag(make_runtime):
    from wots.core.models import CreditLedger, Organization

    rt = make_runtime()
    ctx = internal(rt)
    with pytest.raises(IntegrationConfigError, match="internal_scraper_enabled"):
        run_research(rt, ctx, country="US", trade="plumber", source="scraper")
    with rt.sessions.begin() as s:
        org = s.scalars(select(Organization).where(Organization.id == ctx.org_id)).one()
        org.settings = {**org.settings, "internal_scraper_enabled": True}
    with pytest.raises(IntegrationConfigError, match="input/output format"):
        run_research(rt, internal(rt), country="US", trade="plumber", source="scraper")
    customer = rt.offices.create_office("Customer Co", templates=["web_agency"], ceo_email="c@c.test")
    with pytest.raises(ResearchError, match="no credits"):
        run_research(rt, customer, country="US", trade="plumber", regions=["Portland"])
    with rt.sessions.begin() as s:
        s.add(CreditLedger(org_id=customer.org_id, delta=100, reason="topup", balance_after=100, ts=rt.atlas.clock()))
    with pytest.raises(IntegrationConfigError, match="official APIs only"):
        run_research(rt, customer, country="US", trade="plumber", source="scraper")


# ---------------------------------------------------------------- OpenStreetMap and Companies House


def test_openstreetmap_research_is_free_and_skips_what_it_should(make_runtime, web):
    rt = make_runtime()
    ctx = internal(rt)
    web.osm["portland"] = [
        osm_element(1, "Rose City Plumbing", phone="+1 503 555 0101", addr_housenumber="12", addr_street="Main St",
                    addr_city="Portland", addr_postcode="97201", email="hi@rosecity.test"),
        osm_element(2, "Has A Site Plumbing", website="https://hasasite.com"),
        osm_element(3, "Social Plumbing", **{"contact_facebook": "https://facebook.com/socialplumbing"}),
        osm_element(4, "Sparky Electric", trade=("craft", "electrician")),  # another trade: not returned
        {"type": "way", "id": 5, "tags": {"craft": "plumber"}},  # no name
    ]
    web.unknown_towns.add("atlantis")
    report = run_research(rt, ctx, country="US", trade="plumber", regions=["Portland", "Atlantis"], limit=50)
    assert len(report.created) == 2 and report.skipped == {"has_website": 1}
    assert report.requests == 3 and report.cost_usd == 0  # 2 town lookups + 1 business search
    assert "free" in report.summary() and "OpenStreetMap" in report.summary()
    rose = next(i for i in rt.board.items(ctx) if i.business_name == "Rose City Plumbing")
    assert (rose.source, rose.source_ref, rose.region, rose.postcode) == ("osm", "node/1", "OR", "97201")
    assert rose.email == "hi@rosecity.test" and rose.address == "12 Main St, Portland, 97201"
    social = next(i for i in rt.board.items(ctx) if i.business_name == "Social Plumbing")
    assert social.social_links == {"facebook": "https://facebook.com/socialplumbing"}
    with rt.sessions() as s:
        kinds = s.scalars(select(UsageEvent.kind).where(UsageEvent.org_id == ctx.org_id)).all()
    assert kinds == ["osm_call"] * 3
    again = run_research(rt, ctx, country="US", trade="plumber", regions=["Portland"], limit=50)
    assert again.created == [] and again.skipped["already_on_the_board"] == 2


def test_a_busy_overpass_server_is_retried_then_reported(make_runtime, web):
    from wots.integrations.errors import IntegrationError

    rt = make_runtime()
    web.osm["leeds"] = [osm_element(1, "Leeds Pipes")]
    web.overpass_busy = 2
    report = run_research(rt, internal(rt), country="UK", trade="plumber", regions=["Leeds"])
    assert len(report.created) == 1
    hosts = [r.url.host for r in web.requests if "overpass" in r.url.host]
    assert hosts == ["overpass-api.de", "overpass.private.coffee", "overpass-api.de"]  # tried the backup too
    web.overpass_busy = 10
    with pytest.raises(IntegrationError, match="busy"):
        run_research(rt, internal(rt), country="UK", trade="plumber", regions=["York"])


def test_the_daily_osm_limit(make_runtime, web):
    rt = make_runtime(research={"max_osm_requests_per_day": 3})
    web.osm["leeds"] = [osm_element(1, "Leeds Pipes")]
    report = run_research(rt, internal(rt), country="UK", trade="plumber", regions=["Leeds", "York"], limit=50)
    assert report.requests == 3 and "limit" in report.stopped and len(report.created) == 1


def test_companies_house_finds_active_uk_companies_by_trade(make_runtime, web):
    rt = make_runtime(research={"companies_house_page_size": 2})
    real(rt, "Scout", "Ledger")
    ctx = internal(rt)
    web.ch_by_trade[("43220", "manchester")] = [
        ch_company("01", "PIPEWORKS NORTH LTD", "Manchester"),
        ch_company("02", "FLOW PARTNERS LLP", "Manchester", company_type="llp"),
        ch_company("03", "BIG BOILER HOLDINGS", "Manchester", company_type="private-unlimited"),
    ]
    report = run_research(rt, ctx, country="UK", trade="plumber", regions=["Manchester"], limit=50,
                          source="companies_house")
    assert len(report.created) == 3 and report.requests == 2  # two pages of 2
    before = len(web.hosts("api.company-information.service.gov.uk"))
    items = verify(rt)
    assert len(web.hosts("api.company-information.service.gov.uk")) == before  # already confirmed: no lookups
    assert items["PIPEWORKS NORTH LTD"].status == "ENRICHED" and items["PIPEWORKS NORTH LTD"].registry_id == "01"
    assert items["FLOW PARTNERS LLP"].entity_type == "llp"
    assert items["BIG BOILER HOLDINGS"].disqualify_reason == "entity_not_contactable"


def test_customer_offices_use_their_own_keys(make_runtime, monkeypatch):
    rt = make_runtime()
    customer = rt.offices.create_office("Customer Co", templates=["web_agency"], ceo_email="c@c.test")
    tools = rt.integrations.for_office(customer)
    with pytest.raises(IntegrationConfigError):
        tools.places()  # the internal office's .env key is never lent to customers
    monkeypatch.setenv("SECRETS_KEY", __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode())
    rt.integrations.connect(customer, "places", "customer-places-key")
    assert rt.integrations.for_office(customer).places().api_key == "customer-places-key"
    with rt.sessions() as s:
        stored = s.scalars(select(Integration).where(Integration.org_id == customer.org_id)).one()
    assert "customer-places-key" not in stored.secret_encrypted


# ---------------------------------------------------------------- verification


def test_acceptance_fifty_leads_per_country_with_the_right_ones_dropped(make_runtime, web):
    """Spec v2 §16 Phase 2: 50 leads per country; UK sole traders disqualified with reason;
    real-domain businesses disqualified. Sourced from OpenStreetMap (free)."""
    rt = make_runtime(atlas={"max_jobs_per_org_per_tick": 400})
    real(rt, "Scout", "Ledger")
    ctx = internal(rt)
    for n, (country, region) in enumerate((("US", "Portland"), ("UK", "Manchester"), ("AU", "Melbourne"))):
        web.osm[region.lower()] = [osm_element(n * 1000 + i, f"{region} Plumbing {i}", phone=f"555 01{i:02d}")
                                   for i in range(60)]  # OpenStreetMap ids are unique worldwide
        report = run_research(rt, ctx, country=country, trade="plumber", regions=[region], limit=50)
        assert len(report.created) == 50, (country, report.summary())
    # Some of them turn out not to be leads
    web.sole_traders |= {normalise_name(f"Manchester Plumbing {i}") for i in range(5)}
    web.websites["portlandplumbing3.com"] = "<html><title>Portland Plumbing 3</title>Call us today</html>"
    web.websites["portlandplumbing4.com"] = "<html>This domain is for sale!</html>"  # parked: not theirs
    web.websites["melbourneplumbing7.com.au"] = "<html>Melbourne Plumbing 7 - fast plumbing</html>"
    web.abn[normalise_name("Melbourne Plumbing 9")] = {"abn": "11111111111", "status": "Cancelled"}
    web.abn[normalise_name("Melbourne Plumbing 10")] = None  # not registered: kept

    items = verify(rt, ticks=4)
    by_country = {c: [i for i in items.values() if i.country == c] for c in ("US", "UK", "AU")}
    assert all(len(v) == 50 for v in by_country.values())
    dropped = {name: i.disqualify_reason for name, i in items.items() if i.status == "DISQUALIFIED"}
    assert dropped == {
        **{f"Manchester Plumbing {i}": "uk_sole_trader_or_partnership" for i in range(5)},
        "Portland Plumbing 3": "has_website",
        "Melbourne Plumbing 7": "has_website",
        "Melbourne Plumbing 9": "abn_cancelled",
    }
    enriched = [i for i in items.values() if i.status != "DISQUALIFIED"]
    assert {i.status for i in enriched} == {"ENRICHED"}  # Quill is a fake here, so they wait for copy
    uk = items["Manchester Plumbing 20"]
    assert uk.entity_type == "ltd" and uk.registry_id and uk.checks["registry"]["source"] == "companies_house"
    assert uk.region == "Manchester" and items["Portland Plumbing 20"].region == "OR"
    assert items["Portland Plumbing 4"].status == "ENRICHED"
    assert items["Melbourne Plumbing 10"].entity_type == "unknown"
    assert items["Melbourne Plumbing 11"].entity_type == "company"
    sole = items["Manchester Plumbing 0"]
    note = rt.board.history(ctx, sole.id)[-1].note
    assert "sole trader or partnership" in note and "prior consent" in note
    site = items["Portland Plumbing 3"]
    assert any(d["status"] == "match" for d in site.checks["website"]["domains"])


def test_duplicates_are_dropped_and_the_first_one_kept(make_runtime, clock):
    rt = make_runtime()
    real(rt, "Scout", "Ledger")
    ctx = internal(rt)
    first = new_item(rt, name="Joe's Plumbing", phone="(503) 555-0147")
    clock.advance(seconds=1)
    second = new_item(rt, name="Joes Plumbing LLC", phone="+1 503 555 0147")
    clock.advance(seconds=1)
    other = new_item(rt, name="Joe's Plumbing", phone="(212) 555-0199", address="1 Main St, New York, NY 10001")
    verify(rt)
    assert rt.board.get(ctx, first).status == "ENRICHED"
    dup = rt.board.get(ctx, second)
    assert dup.status == "DISQUALIFIED" and dup.disqualify_reason == "duplicate"
    assert dup.checks["dedupe"][0]["id"] == first
    assert rt.board.get(ctx, other).status == "ENRICHED"  # same name, different business


def test_a_social_profile_is_not_a_website(make_runtime):
    rt = make_runtime()
    real(rt, "Scout", "Ledger")
    ctx = internal(rt)
    item = new_item(rt, website_found="https://www.instagram.com/joesplumbing")
    verify(rt)
    lead = rt.board.get(ctx, item)
    assert lead.status == "ENRICHED" and lead.website_found is None
    assert lead.social_links == {"instagram": "https://www.instagram.com/joesplumbing"}


def test_a_listed_website_or_a_matching_domain_disqualifies(make_runtime, web):
    rt = make_runtime()
    real(rt, "Scout", "Ledger")
    ctx = internal(rt)
    listed = new_item(rt, name="Listed Co", website_found="https://listed.example")
    by_phone = new_item(rt, name="Acme Roofing", phone="(503) 555-0123")
    web.websites["acmeroofing.com"] = "<html>Call (503) 555-0123 for a quote</html>"
    unrelated = new_item(rt, name="Sam Electric")
    web.websites["samelectric.com"] = "<html>Welcome to a different company entirely</html>"
    verify(rt)
    assert rt.board.get(ctx, listed).disqualify_reason == "has_website"
    assert rt.board.get(ctx, by_phone).disqualify_reason == "has_website"
    assert rt.board.get(ctx, unrelated).status == "ENRICHED"


def test_a_dissolved_uk_company_is_dropped(make_runtime, web):
    rt = make_runtime()
    real(rt, "Scout", "Ledger")
    ctx = internal(rt)
    item = new_item(rt, name="Old Boiler Co", country="UK")
    web.companies[normalise_name("Old Boiler Co")] = {"company_status": "dissolved"}
    verify(rt)
    assert rt.board.get(ctx, item).disqualify_reason == "company_not_active"


def test_a_uk_llp_is_contactable_but_an_unlimited_company_is_not(make_runtime, web):
    rt = make_runtime()
    real(rt, "Scout", "Ledger")
    ctx = internal(rt)
    llp = new_item(rt, name="Fox Partners", country="UK")
    unlimited = new_item(rt, name="Oak Holdings", country="UK")
    web.companies[normalise_name("Fox Partners")] = {"company_type": "llp"}
    web.companies[normalise_name("Oak Holdings")] = {"company_type": "private-unlimited"}
    verify(rt)
    assert rt.board.get(ctx, llp).entity_type == "llp"
    assert rt.board.get(ctx, unlimited).disqualify_reason == "entity_not_contactable"


def test_a_missing_registry_key_pauses_only_that_work(make_runtime, monkeypatch):
    rt = make_runtime()
    real(rt, "Scout", "Ledger")
    ctx = internal(rt)
    monkeypatch.delenv("COMPANIES_HOUSE_KEY")
    uk = [new_item(rt, name=f"UK Biz {i}", country="UK") for i in range(3)]
    us = new_item(rt, name="US Biz")
    report = None
    for _ in range(3):
        report = rt.atlas.tick()
    assert {rt.board.get(ctx, i).status for i in uk} == {"VERIFY"}  # waiting, not escalated
    assert rt.board.get(ctx, us).status == "ENRICHED"
    assert "COMPANIES_HOUSE_KEY" in report.integrations_unavailable["wots-office"]
    assert sum("Companies House" in text for _, text in rt.notifier.sent) == 1
    monkeypatch.setenv("COMPANIES_HOUSE_KEY", "now-set")
    rt.atlas.tick()
    assert {rt.board.get(ctx, i).status for i in uk} == {"ENRICHED"}


def test_dedupe_stays_in_the_office(make_runtime, web):
    """Dedupe only compares leads within one office."""
    from .conftest import fake_team

    rt = make_runtime()
    real(rt, "Scout", "Ledger")
    mine = internal(rt)
    other = rt.offices.create_office("Other Office", templates=["web_agency"], ceo_email="o@o.test")
    fake_team(rt, other)
    new_item(rt, ctx=other, name="Joe's Plumbing", phone="(503) 555-0147")
    item = new_item(rt, ctx=mine, name="Joe's Plumbing", phone="(503) 555-0147")
    force_status(rt, mine, item, "VERIFY")
    rt.atlas.tick("wots-office")
    assert rt.board.get(mine, item).status == "ENRICHED"
