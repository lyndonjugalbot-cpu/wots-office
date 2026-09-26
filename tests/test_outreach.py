"""Phase 4: outreach, manual send (spec v2 §8, §11, §16). Echo drafts compliant pitches per country;
the CEO approves them; they're sent from the office mailbox only in the recipient's local window,
never to suppressed addresses; one follow-up; replies and opt-outs are read from the mailbox."""
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from wots.core import auth
from wots.core.config import load_config
from wots.core.outreach import next_window
from wots.dashboard import actions
from wots.dashboard.app import create_app
from wots.integrations.email import IncomingEmail, parse_incoming

from .conftest import as_user, force_status, internal, new_item

POSTAL = "1 Queen Street, Auckland 1010, New Zealand"
LEADS = {  # country -> (business, timezone, source)
    "US": ("Rose City Plumbing", "America/Los_Angeles", "osm"),
    "UK": ("Pipeworks North Ltd", "Europe/London", "companies_house"),
    "AU": ("Geelong Hair Co", "Australia/Melbourne", "osm"),
}


class ScriptedEcho:
    """Claude for Echo: an honest subject and opener built from the record it was given."""

    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        record = json.loads(kw["messages"][0]["content"].split("\n", 1)[1].split("\n\n")[0])
        body = {"subject": f"A website idea for {record['business_name']}",
                "opener": f"Hope things are going well at {record['business_name']}."}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(body))],
                               usage=SimpleNamespace(input_tokens=300, output_tokens=80), stop_reason="end_turn")


class FakeMailbox:
    live = True

    def __init__(self):
        self.sent = []

    def send(self, mail):
        self.sent.append(mail)
        return f"<msg{len(self.sent)}@wots.test>"


class FakeImap:
    def __init__(self):
        self.inbox: list[IncomingEmail] = []

    def since(self, day):
        return list(self.inbox)


@pytest.fixture
def office(make_runtime, monkeypatch):
    monkeypatch.setenv("OUTREACH_FROM", "Lyndon at Wots Office <lyndon@wots.test>")
    claude = ScriptedEcho()
    rt = make_runtime(dry_run=False, llm_client=claude)
    ctx = internal(rt)
    rt.offices.accept_outreach_terms(ctx, POSTAL)
    echo = rt.offices.hire(ctx, "cold_email", "Echo")
    rt.mailbox, rt.imap, rt.claude, rt.echo_id = FakeMailbox(), FakeImap(), claude, echo.id
    rt.integrations.email_sender, rt.integrations.imap_reader = rt.mailbox, rt.imap
    return rt


def ctx_of(rt):
    return internal(rt)  # re-read: accepting the terms changed the office settings


def deployed(rt, country, email=None, **fields):
    name, tz, source = LEADS[country]
    source = fields.pop("source", source)
    item = new_item(rt, name=name, country=country, timezone=tz, source=source, category="Plumber",
                    email=email or f"hello@{name.split()[0].lower()}.test", **fields)
    force_status(rt, ctx_of(rt), item, "PREVIEW_DEPLOYED")
    rt.board.update_profile(ctx_of(rt), item, {"preview_url": f"https://{name.split()[0].lower()}-abc123.wots-previews.pages.dev",
                                               "checks": {"preview": {"live": True}}}, "system", "test", "deployed")
    return item


def pitch(rt, item):
    return rt.integrations.for_office(ctx_of(rt)).outreach().messages(item)


def ceo(rt):
    return as_user(rt, ctx_of(rt))


# ---------------------------------------------------------------- send windows


@pytest.mark.parametrize("utc,tz,expected", [
    ("2026-09-01 17:00", "America/Los_Angeles", None),  # Tue 10:00 PDT: inside
    ("2026-09-01 00:00", "America/Los_Angeles", "2026-09-01 16:00"),  # Mon 17:00 PDT -> Tue 09:00
    ("2026-09-01 00:00", "Europe/London", "2026-09-01 08:00"),  # Tue 01:00 BST -> 09:00
    ("2026-09-01 00:00", "Australia/Melbourne", None),  # Tue 10:00 AEST: inside
    ("2026-09-03 12:00", "Europe/London", "2026-09-08 08:00"),  # Thu 13:00 -> next Tue
])
def test_the_next_send_window(utc, tz, expected):
    window = load_config().countries["US"].send_window_local
    got = next_window(datetime.fromisoformat(utc), tz, window)
    assert (got.strftime("%Y-%m-%d %H:%M") if got else None) == expected


# ---------------------------------------------------------------- drafting


def test_acceptance_correct_pitches_for_each_country(office):
    rt = office
    items = {c: deployed(rt, c) for c in LEADS}
    rt.atlas.tick()
    for country, item in items.items():
        lead = rt.board.get(ctx_of(rt), item)
        assert lead.status == "PITCH_DRAFTED", (country, rt.board.history(ctx_of(rt), item)[-1].note)
        msgs = pitch(rt, item)
        initial, followup = msgs["initial"], msgs["followup"]
        assert initial.subject == f"A website idea for {lead.business_name}" and followup.subject == f"Re: {initial.subject}"
        body = initial.body
        assert lead.preview_url in body and lead.business_name in body
        assert POSTAL in body and "Wots Office" in body and 'reply "unsubscribe"' in body  # all countries
        assert POSTAL in followup.body and "unsubscribe" in followup.body
        if country == "UK":
            assert "We found Pipeworks North Ltd's details on the Companies House register." in body
        else:
            assert "We found" not in body  # source disclosure is a UK rule
        assert "£" not in body and "$" not in body  # no pricing configured
    systems = {c["system"] for c in rt.claude.calls}
    assert any("en-GB spelling" in s for s in systems) and any("en-AU spelling" in s for s in systems)
    assert all("never invent" in s.lower() for s in systems)


def test_pricing_per_country_when_configured(make_runtime, monkeypatch):
    monkeypatch.setenv("OUTREACH_FROM", "Lyndon <lyndon@wots.test>")
    rt = make_runtime(dry_run=False, llm_client=ScriptedEcho(), outreach={"pricing": {"UK": "£350 one-off"}})
    ctx = internal(rt)
    rt.offices.accept_outreach_terms(ctx, POSTAL)
    rt.offices.hire(ctx, "cold_email", "Echo")
    item = deployed(rt, "UK")
    rt.atlas.tick()
    assert "it's £350 one-off" in pitch(rt, item)["initial"].body


def test_suppressed_addresses_are_blocked_at_draft(office):
    rt = office
    desk = rt.integrations.for_office(ctx_of(rt)).outreach()
    desk.suppress("hello@rose.test", "opted out last year")
    item = deployed(rt, "US")
    rt.atlas.tick()
    lead = rt.board.get(ctx_of(rt), item)
    assert (lead.status, lead.disqualify_reason) == ("DISQUALIFIED", "suppressed")
    assert pitch(rt, item) == {} and rt.claude.calls == []


def test_a_whole_domain_can_be_suppressed_but_not_webmail(office):
    desk = office.integrations.for_office(ctx_of(office)).outreach()
    desk.suppress("boss@bigco.test", "company asked", whole_domain=True)
    assert desk.suppressed("anyone@bigco.test") == "company asked"
    with pytest.raises(ValueError, match="webmail"):
        desk.suppress("someone@gmail.com", "x", whole_domain=True)


def test_no_email_means_the_ceo_is_asked(office):
    rt = office
    item = deployed(rt, "US")
    rt.board.update_profile(ctx_of(rt), item, {"email": None}, "system", "test", "no email")
    rt.atlas.tick()
    lead = rt.board.get(ctx_of(rt), item)
    assert lead.status == "ESCALATED" and "No email address" in rt.board.history(ctx_of(rt), item)[-1].note


def test_au_emails_must_be_published_by_the_business(office):
    rt = office
    item = deployed(rt, "AU", source="csv:list.csv")  # from a list: we can't tell where it's published
    rt.atlas.tick()
    assert rt.board.get(ctx_of(rt), item).status == "ESCALATED"
    assert "publishes itself" in rt.board.history(ctx_of(rt), item)[-1].note

    client = signed_in(rt)
    client.post(f"/api/items/{item}/contact", json={"email": "hello@geelong.test",
                                                    "found_at": "https://facebook.com/geelonghairco/about"})
    assert client.post(f"/api/items/{item}/resolve", json={"to_status": "PREVIEW_DEPLOYED"}).status_code == 200
    rt.atlas.tick()
    assert rt.board.get(ctx_of(rt), item).status == "PITCH_DRAFTED"


def test_uk_pitches_must_say_where_we_found_them(office):
    rt = office
    item = deployed(rt, "UK", source="csv:list.csv")
    rt.atlas.tick()
    assert rt.board.get(ctx_of(rt), item).status == "ESCALATED"
    assert "where we found" in rt.board.history(ctx_of(rt), item)[-1].note
    signed_in(rt).post(f"/api/items/{item}/contact", json={"email": "hello@pipeworks.test",
                                                            "found_at": "yell.com"})
    actions.resolve_escalation(rt.board, ceo(rt), item, "PREVIEW_DEPLOYED")
    rt.atlas.tick()
    assert "We found Pipeworks North Ltd's details on yell.com." in pitch(rt, item)["initial"].body


def test_the_pitch_reads_well(office):
    rt = office
    item = deployed(rt, "US")
    rt.atlas.tick()
    body = pitch(rt, item)["initial"].body
    assert "\n\nIf you'd like to keep it" in body and "\n\n\n" not in body


def test_a_ceo_rejection_is_redrafted_with_the_notes(office):
    rt = office
    item = deployed(rt, "US")
    rt.atlas.tick()
    actions.reject_pitch(rt.board, ceo(rt), item, "Mention that they can keep their own domain")
    rt.atlas.tick()
    assert rt.board.get(ctx_of(rt), item).status == "PITCH_DRAFTED"
    assert "Mention that they can keep their own domain" in rt.claude.calls[-1]["messages"][0]["content"]
    with rt.sessions() as s:
        from sqlalchemy import select

        from wots.core.models import Outreach
        statuses = sorted(s.scalars(select(Outreach.status).where(Outreach.org_id == ctx_of(rt).org_id)))
    assert statuses == ["discarded", "discarded", "draft", "draft"]


# ---------------------------------------------------------------- sending


def approve_all(rt):
    for item in rt.board.items(ctx_of(rt), statuses=["PITCH_DRAFTED"]):
        actions.approve_pitch(rt.board, rt.integrations.for_office(ctx_of(rt)).outreach(), ceo(rt), item.id)


def test_acceptance_sends_land_in_the_recipients_local_window(office, clock):
    """The clock starts Tue 1 Sep 2026 00:00 UTC: 10:00 in Melbourne, 01:00 in London, Mon 17:00 in Portland."""
    rt = office
    items = {c: deployed(rt, c) for c in LEADS}
    rt.atlas.tick()
    approve_all(rt)
    rt.atlas.tick()
    assert [m.to for m in rt.mailbox.sent] == ["hello@geelong.test"]
    assert rt.board.get(ctx_of(rt), items["UK"]).status == "PITCH_APPROVED"
    assert "Scheduled for the recipient's send window" in rt.board.history(ctx_of(rt), items["UK"])[-1].note
    rt.atlas.tick()  # still waiting: no repeat notes
    assert sum("Scheduled" in e.note for e in rt.board.history(ctx_of(rt), items["UK"]) if e.note) == 1

    clock.advance(hours=8)  # 09:00 in London
    rt.atlas.tick()
    assert [m.to for m in rt.mailbox.sent][-1] == "hello@pipeworks.test"
    clock.advance(hours=8)  # 09:00 in Portland
    rt.atlas.tick()
    assert [m.to for m in rt.mailbox.sent][-1] == "hello@rose.test"

    for country, item in items.items():
        assert rt.board.get(ctx_of(rt), item).status == "PITCHED"
        sent = pitch(rt, item)["initial"].sent_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(LEADS[country][1]))
        assert sent.strftime("%a") in {"Tue", "Wed", "Thu"} and 9 <= sent.hour < 11, (country, sent)
    mail = rt.mailbox.sent[0].message()
    assert mail["List-Unsubscribe"] == "<mailto:lyndon@wots.test?subject=unsubscribe>"
    assert mail["From"] == "Lyndon at Wots Office <lyndon@wots.test>"


def test_suppressed_addresses_are_blocked_at_send(office):
    rt = office
    item = deployed(rt, "AU")
    rt.atlas.tick()
    approve_all(rt)
    rt.integrations.for_office(ctx_of(rt)).outreach().suppress("hello@geelong.test", "replied STOP to another email")
    rt.atlas.tick()
    lead = rt.board.get(ctx_of(rt), item)
    assert (lead.status, lead.disqualify_reason) == ("DISQUALIFIED", "suppressed")
    assert rt.mailbox.sent == []


def test_the_daily_send_cap(make_runtime, monkeypatch, clock):
    monkeypatch.setenv("OUTREACH_FROM", "Lyndon <lyndon@wots.test>")
    rt = make_runtime(dry_run=False, llm_client=ScriptedEcho(), outreach={"daily_send_cap": 1})
    ctx = internal(rt)
    rt.offices.accept_outreach_terms(ctx, POSTAL)
    rt.offices.hire(ctx, "cold_email", "Echo")
    rt.integrations.email_sender = mailbox = FakeMailbox()
    first = deployed(rt, "AU", email="a@one.test")
    second = new_item(rt, name="Second Salon", country="AU", timezone="Australia/Melbourne", source="osm", email="b@two.test")
    force_status(rt, internal(rt), second, "PREVIEW_DEPLOYED")
    rt.board.update_profile(internal(rt), second, {"preview_url": "https://second.pages.dev"}, "system", "t", "x")
    rt.atlas.tick()
    approve_all(rt)
    rt.atlas.tick()
    assert len(mailbox.sent) == 1
    waiting = second if rt.board.get(internal(rt), first).status == "PITCHED" else first
    assert "send cap" in rt.board.history(internal(rt), waiting)[-1].note
    clock.advance(days=1)  # Wed 10:00 in Melbourne
    rt.atlas.tick()
    assert len(mailbox.sent) == 2


def test_dry_run_saves_to_the_outbox_and_never_leaks_a_placeholder(make_runtime, monkeypatch):
    rt = make_runtime(dry_run=True, llm_client=ScriptedEcho())
    ctx = internal(rt)
    rt.offices.accept_outreach_terms(ctx, POSTAL)
    rt.offices.hire(ctx, "cold_email", "Echo")
    item = new_item(rt, name="Geelong Hair Co", country="AU", timezone="Australia/Melbourne", source="osm",
                    email="hello@geelong.test")
    force_status(rt, internal(rt), item, "PREVIEW_DEPLOYED")  # dry run: no preview URL
    rt.atlas.tick()
    body = pitch(rt, item)["initial"].body
    assert "published once DRY_RUN is off" in body
    approve_all(rt)
    rt.atlas.tick()
    assert rt.board.get(internal(rt), item).status == "PITCHED"
    emails = list((rt.config.settings.data_path / "outbox" / ctx.org_id / "emails").glob("*.eml"))
    assert len(emails) == 1
    saved = parse_incoming(emails[0].read_bytes())
    assert saved.subject == "A website idea for Geelong Hair Co" and "unsubscribe" in saved.text
    assert pitch(rt, item)["initial"].status == "saved_to_outbox"


def test_a_dry_run_draft_is_never_sent_for_real(office):
    rt = office
    item = deployed(rt, "AU")
    rt.atlas.tick()
    desk = rt.integrations.for_office(ctx_of(rt)).outreach()
    msg = desk.messages(item)["initial"]
    edits = {"initial": {"body": msg.body.replace(rt.board.get(ctx_of(rt), item).preview_url,
                                                   "[preview link: published once DRY_RUN is off]")}}
    actions.approve_pitch(rt.board, desk, ceo(rt), item, edits)
    rt.atlas.tick()
    assert rt.board.get(ctx_of(rt), item).status == "ESCALATED" and rt.mailbox.sent == []


# ---------------------------------------------------------------- after sending


def test_one_follow_up_then_lost(office, clock):
    rt = office
    item = deployed(rt, "AU")
    rt.atlas.tick()
    approve_all(rt)
    rt.atlas.tick()
    assert len(rt.mailbox.sent) == 1
    clock.advance(days=4)  # Saturday: not due yet
    rt.atlas.tick()
    assert len(rt.mailbox.sent) == 1
    clock.advance(days=3)  # Tue 8 Sep, 10:00 in Melbourne: due and in the window
    rt.atlas.tick()
    assert len(rt.mailbox.sent) == 2
    follow = rt.mailbox.sent[1]
    assert follow.subject.startswith("Re: ") and follow.in_reply_to == "<msg1@wots.test>" and not follow.attachments
    assert rt.board.get(ctx_of(rt), item).status == "PITCHED"
    clock.advance(days=5)
    rt.atlas.tick()
    assert rt.board.get(ctx_of(rt), item).status == "LOST"
    assert "No reply after the follow-up" in rt.board.history(ctx_of(rt), item)[-1].note


def test_replies_opt_outs_bounces_and_auto_replies(office, clock):
    rt = office
    names = {"reply": "a@reply.test", "optout": "b@optout.test", "bounce": "c@bounce.test", "auto": "d@auto.test"}
    items = {}
    for i, (key, addr) in enumerate(names.items()):
        item = new_item(rt, name=f"Salon {key}", country="AU", timezone="Australia/Melbourne", source="osm", email=addr)
        force_status(rt, ctx_of(rt), item, "PREVIEW_DEPLOYED")
        rt.board.update_profile(ctx_of(rt), item, {"preview_url": f"https://s{i}.pages.dev"}, "system", "t", "x")
        items[key] = item
    rt.atlas.tick()
    approve_all(rt)
    rt.atlas.tick()
    assert len(rt.mailbox.sent) == 4
    rt.imap.inbox = [
        IncomingEmail("a@reply.test", "Re: A website idea", "Looks great! How much would it be?\n> original"),
        IncomingEmail("b@optout.test", "Re: A website idea", "Please remove me from your list."),
        IncomingEmail("mailer-daemon@mx.test", "Undeliverable: A website idea", "Delivery to c@bounce.test failed"),
        IncomingEmail("d@auto.test", "Automatic reply: A website idea", "I'm away until Monday.", auto_submitted=True),
    ]
    clock.advance(minutes=10)
    rt.atlas.tick()
    status = {k: rt.board.get(ctx_of(rt), i).status for k, i in items.items()}
    assert status == {"reply": "REPLIED", "optout": "LOST", "bounce": "LOST", "auto": "PITCHED"}
    desk = rt.integrations.for_office(ctx_of(rt)).outreach()
    assert desk.suppressed("b@optout.test") == "opted out by reply" and desk.suppressed("c@bounce.test") == "bounced"
    assert desk.suppressed("a@reply.test") is None
    assert "How much would it be?" in rt.board.history(ctx_of(rt), items["reply"])[-1].note
    assert any("REPLIED" in text for _, text in rt.notifier.sent)  # the CEO hears about it
    rt.atlas.tick()  # reading again doesn't repeat anything
    assert rt.board.get(ctx_of(rt), items["reply"]).status == "REPLIED"


def test_messages_are_classified():
    assert IncomingEmail("x@y.test", "Re: hi", "STOP emailing me").kind == "opt_out"
    assert IncomingEmail("x@y.test", "Re: hi", "Not interested, thanks").kind == "opt_out"
    assert IncomingEmail("x@y.test", "Re: hi", "Yes please, call me\n> unsubscribe").kind == "reply"  # quoted footer
    assert IncomingEmail("postmaster@x.test", "Returned mail", "").kind == "bounce"
    assert IncomingEmail("x@y.test", "Out of office", "").kind == "auto"


# ---------------------------------------------------------------- the pitch queue (dashboard)


def signed_in(rt, email=None):
    client = TestClient(create_app(rt))
    client.get(auth.login_link(rt, email, "http://testserver").replace("http://testserver", ""), follow_redirects=False)
    return client


def test_the_pitch_queue(office):
    rt = office
    item = deployed(rt, "UK")
    rt.atlas.tick()
    client = signed_in(rt)
    assert client.get("/api/overview").json()["pending"]["pitches"] == 1
    detail = client.get(f"/api/items/{item}").json()
    assert detail["can_decide"] and detail["pitch"]["initial"]["status"] == "draft"
    body = detail["pitch"]["initial"]["body"]
    bad = client.post(f"/api/items/{item}/pitch/approve",
                      json={"edits": {"initial": {"body": body.split("--")[0]}}})  # footer removed
    assert bad.status_code == 409 and "opt out" in bad.json()["detail"]
    edited = body.replace("Would you like", "Shall I")
    ok = client.post(f"/api/items/{item}/pitch/approve",
                     json={"edits": {"initial": {"subject": "Your new website", "body": edited}}})
    assert ok.json()["status"] == "PITCH_APPROVED"
    msg = pitch(rt, item)["initial"]
    assert (msg.subject, msg.status) == ("Your new website", "approved") and "Shall I" in msg.body


def test_replies_are_closed_by_the_ceo(office):
    rt = office
    item = deployed(rt, "AU")
    rt.atlas.tick()
    approve_all(rt)
    rt.atlas.tick()
    client = signed_in(rt)
    assert client.post(f"/api/items/{item}/replied", json={"notes": "They called"}).json()["status"] == "REPLIED"
    assert client.get("/api/overview").json()["pending"]["replies"] == 1
    assert client.post(f"/api/items/{item}/close", json={"won": True, "notes": "Paid"}).json()["status"] == "WON"


def test_suppression_from_the_dashboard(office):
    rt = office
    item = deployed(rt, "AU")
    rt.atlas.tick()
    approve_all(rt)
    rt.atlas.tick()
    client = signed_in(rt)
    assert client.post(f"/api/items/{item}/suppress", json={"reason": "asked by phone"}).json()["status"] == "LOST"
    listed = client.get("/api/suppression").json()
    assert [(e["email"], e["reason"]) for e in listed] == [("hello@geelong.test", "asked by phone")]
    assert client.post("/api/suppression", json={"email": "x@gmail.com", "whole_domain": True}).status_code == 400
    assert client.post("/api/suppression", json={"email": "sales@bigco.test", "whole_domain": True}).json() == {"ok": True}
    assert client.delete(f"/api/suppression/{listed[0]['id']}").json() == {"ok": True}
    viewer = rt.offices.ensure_user("viewer@wots.test")
    rt.offices.add_member(ctx_of(rt), viewer.id, "viewer")
    assert signed_in(rt, "viewer@wots.test").post("/api/suppression", json={"email": "a@b.test"}).status_code == 403
