"""Phase 3: assets and previews (spec v2 §8, §16). The Graphic Designer's logo/hero/share image, and
Dock publishing approved sites as noindexed Cloudflare Pages previews, then taking them down."""
import json

import pytest

from wots.employees.base import EmployeeResult
from wots.integrations.previews import branch_alias

from .conftest import behave, employee_id, force_status, internal, new_item

SITE = """<!doctype html><html><head><meta name="robots" content="noindex, nofollow">
<meta property="og:image" content="assets/og.png"><title>Joe's</title></head><body><h1>Joe's Plumbing</h1></body></html>"""


def approved_site(rt, name="Joe's Plumbing", html=SITE):
    ctx = internal(rt)
    item = new_item(rt, name=name)
    site = rt.files.item_dir(ctx.org_id, item) / "site"
    site.mkdir(parents=True)
    (site / "index.html").write_text(html)
    force_status(rt, ctx, item, "APPROVED")
    return item


def real(rt, *names):
    ctx = internal(rt)
    for name in names:
        rt.atlas.impl_overrides.pop(employee_id(rt, ctx, name))


def test_branch_aliases_fit_cloudflares_limit():
    alias = branch_alias("The Very Long Named Plumbing & Heating Company of Greater Manchester Ltd", "1f2e3d4c-aaaa")
    assert len(alias) <= 28 and alias.endswith("-1f2e3d") and alias == alias.lower()
    assert branch_alias("Joe's Café", "abcdef12-0000") == "joes-cafe-abcdef"


def test_dry_run_keeps_the_preview_local_and_gives_no_url(make_runtime):
    rt = make_runtime(dry_run=True)
    real(rt, "Dock")
    ctx = internal(rt)
    item = approved_site(rt)
    rt.atlas.tick()
    lead = rt.board.get(ctx, item)
    assert lead.status == "PREVIEW_DEPLOYED" and lead.preview_url is None
    assert lead.checks["preview"]["live"] is False and lead.checks["preview"]["host"] == "local"
    local = rt.config.settings.data_path / "previews" / ctx.org_id / lead.checks["preview"]["alias"]
    assert (local / "_headers").read_text().count("noindex") == 1 and (local / "robots.txt").exists()
    assert "Dry run" in rt.board.history(ctx, item)[-1].note


def test_acceptance_approved_sites_get_working_noindexed_preview_urls(make_runtime, web):
    """Spec v2 §16 Phase 3: approved sites get working, non-indexable preview URLs."""
    rt = make_runtime(dry_run=False)
    real(rt, "Dock")
    ctx = internal(rt)
    first, second = approved_site(rt), approved_site(rt, name="Kettle Café")
    rt.atlas.tick()
    for item in (first, second):
        lead = rt.board.get(ctx, item)
        alias = branch_alias(lead.business_name, lead.id)
        assert lead.status == "PREVIEW_DEPLOYED"
        assert lead.preview_url == f"https://{alias}.wots-previews.pages.dev"
        assert lead.checks["preview"]["noindex"] == {"status": 200, "x_robots_tag": True, "robots_meta": True}
        assert lead.checks["preview"]["deployment_url"].endswith(".wots-previews.pages.dev")
        published = web.websites[f"{alias}.wots-previews.pages.dev"]
        assert f'content="{lead.preview_url}/assets/og.png"' in published  # absolute, for link previews
        site = rt.files.item_dir(ctx.org_id, item) / "site"
        assert (site / "index.html").read_text() == SITE and not (site / "_headers").exists()  # the QA'd site is untouched
    assert list(web.pages_projects) == ["wots-previews"]  # one project, created once
    assert len([r for r in web.requests if r.method == "POST" and r.url.host == "api.cloudflare.com"]) == 1


def test_a_taken_project_name_uses_the_subdomain_cloudflare_gave(make_runtime, web):
    web.pages_taken = True
    rt = make_runtime(dry_run=False)
    real(rt, "Dock")
    item = approved_site(rt)
    rt.atlas.tick()
    assert rt.board.get(internal(rt), item).preview_url.endswith(".wots-previews-7xq.pages.dev")


def test_without_cloudflare_the_deploy_waits_and_the_ceo_is_told(make_runtime, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN")
    rt = make_runtime(dry_run=False)
    real(rt, "Dock")
    item = approved_site(rt)
    for _ in range(3):
        report = rt.atlas.tick()
    assert rt.board.get(internal(rt), item).status == "APPROVED"
    assert "CLOUDFLARE_API_TOKEN" in report.integrations_unavailable["wots-office"]
    assert sum("Cloudflare Pages" in text for _, text in rt.notifier.sent) == 1


def test_a_rejected_token_is_a_setup_problem(make_runtime, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "wrong")
    rt = make_runtime(dry_run=False)
    real(rt, "Dock")
    item = approved_site(rt)
    report = rt.atlas.tick()
    assert "refused the API token" in report.integrations_unavailable["wots-office"]
    assert rt.board.get(internal(rt), item).status == "APPROVED"


def test_a_site_without_noindex_is_never_published(make_runtime, web):
    rt = make_runtime(dry_run=False)
    real(rt, "Dock")
    ctx = internal(rt)
    item = approved_site(rt, html="<html><body>No robots tag</body></html>")
    report = rt.atlas.tick()
    assert report.failures == 1 and rt.board.get(ctx, item).status == "APPROVED"
    assert "noindex" in rt.board.history(ctx, item)[-1].note
    assert web.pages_deployments == []


def test_old_previews_of_lost_leads_are_taken_down(make_runtime, web, clock):
    rt = make_runtime(dry_run=False)
    real(rt, "Dock")
    ctx = internal(rt)
    lost, kept, recent = approved_site(rt, "Lost Co"), approved_site(rt, "Won Co"), approved_site(rt, "Recent Co")
    rt.atlas.tick()
    assert len(web.pages_deployments) == 3
    for item in (lost, kept):
        force_status(rt, ctx, item, "LOST" if item == lost else "WON")
    clock.advance(days=rt.config.settings.previews.preview_ttl_days + 1)
    rt.board.transition(ctx, recent, "PITCH_DRAFTED", "system", "test")  # deployed long ago but not lost
    rt.atlas.tick()
    gone = rt.board.get(ctx, lost)
    assert gone.preview_url is None and gone.checks["preview"]["removed_at"]
    assert "taken down" in rt.board.history(ctx, lost)[-1].note
    assert {d["branch"] for d in web.pages_deployments} == {
        branch_alias("Won Co", kept), branch_alias("Recent Co", recent)}
    assert rt.board.get(ctx, kept).preview_url  # won: stays up
    rt.atlas.tick()  # nothing left to do
    assert len(web.pages_deployments) == 2


def test_a_young_lost_preview_stays_up(make_runtime, web, clock):
    rt = make_runtime(dry_run=False)
    real(rt, "Dock")
    ctx = internal(rt)
    item = approved_site(rt)
    rt.atlas.tick()
    force_status(rt, ctx, item, "LOST")
    clock.advance(days=3)
    rt.atlas.tick()
    assert rt.board.get(ctx, item).preview_url and len(web.pages_deployments) == 1


# ---------------------------------------------------------------- design


def test_the_designer_draws_a_logo_hero_and_share_image(make_runtime):
    rt = make_runtime()
    real(rt, "Iris")
    ctx = internal(rt)
    item = new_item(rt, name="Salt & Sage Day Spa", category="Day spa")
    folder = rt.files.item_dir(ctx.org_id, item)
    (folder / "copy.json").write_text(json.dumps({"headline": "Calm, local, unhurried"}))
    force_status(rt, ctx, item, "COPY_READY")
    rt.atlas.tick()
    assert rt.board.get(ctx, item).status == "ASSETS_READY"
    assets = folder / "assets"
    assert {p.name for p in assets.iterdir()} >= {"brand.json", "logo.svg", "mark.svg", "hero.svg", "logo.png",
                                                  "hero.png", "og.png"}
    def png_size(path):  # width and height from the PNG header
        data = path.read_bytes()[16:24]
        return int.from_bytes(data[:4], "big"), int.from_bytes(data[4:], "big")

    assert png_size(assets / "og.png") == (1200, 630)
    assert png_size(assets / "hero.png") == (1200, 900)  # 600x450 at 2x
    manifest = json.loads((folder / "design_manifest.json").read_text())
    assert manifest["photos"] == [] and manifest["style_profile"] == "flat_brand"
    assert "<text" not in (assets / "hero.svg").read_text()  # decorative: no words, no fake photos


@pytest.mark.parametrize("style", ["flat_brand", "bold_playful", "minimal"])
def test_each_designer_style_draws_differently(make_runtime, style):
    rt = make_runtime()
    ctx = internal(rt)
    iris = employee_id(rt, ctx, "Iris")
    rt.offices.update_employee(ctx, iris, {"style_profile": style})
    real(rt, "Iris")
    item = new_item(rt, name="Harbour Line Plumbing", category="Plumber")
    force_status(rt, ctx, item, "COPY_READY")
    rt.atlas.tick()
    logo = (rt.files.item_dir(ctx.org_id, item) / "assets" / "logo.svg").read_text()
    assert {"flat_brand": "<circle", "bold_playful": "<path", "minimal": "<rect"}[style] in logo


def test_the_site_uses_the_designers_work(make_runtime):
    rt = make_runtime()
    real(rt, "Iris", "Pixel", "Nova")
    ctx = internal(rt)
    item = new_item(rt, name="Harbour Line Plumbing", category="Plumber", phone="(503) 555-0147")
    folder = rt.files.item_dir(ctx.org_id, item)
    (folder / "copy.json").write_text(json.dumps({
        "headline": "Plumbing you can count on", "subheadline": "Local repairs", "about": "We fix pipes.",
        "services": [{"name": "Leaks", "description": "Fast."}], "cta": "Call us", "seo_title": "Harbour Line",
        "meta_description": "x" * 120, "assumptions": [], "locale": "en-US"}))
    force_status(rt, ctx, item, "COPY_READY")
    behave(rt, "Hawk", lambda i, c: EmployeeResult(None))
    rt.atlas.tick()
    rt.atlas.tick()
    html = (folder / "site" / "index.html").read_text()
    assert 'class="hero-art" src="assets/hero.svg" alt=""' in html
    assert 'href="assets/mark.svg"' in html and 'property="og:image" content="assets/og.png"' in html
    assert {p.name for p in (folder / "site" / "assets").iterdir()} == {"logo.svg", "mark.svg", "hero.svg", "og.png"}
