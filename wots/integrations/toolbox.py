"""Each office's integration clients (spec v2 §4, §8).

Keys come from the office's `integrations` rows (encrypted with SECRETS_KEY). The internal office
falls back to .env (GOOGLE_PLACES_KEY, COMPANIES_HOUSE_KEY, ABN_GUID). Tests pass an httpx transport
so nothing reaches the network.
"""
from __future__ import annotations

import os

import httpx
from sqlalchemy import select

from ..core.context import OrgContext
from ..core.lead_identity import extract_postcode, normalise_name, normalise_postcode, phone_digits
from ..core.models import Integration, LeadProfile, WorkItem
from ..core.secrets import decrypt, encrypt
from .abn import AbnClient
from .companies_house import CompaniesHouseClient
from .osm import OsmClient
from .previews import CloudflarePagesHost, LocalPreviewHost
from .places import PlacesClient

ENV_KEYS = {"places": "GOOGLE_PLACES_KEY", "companies_house": "COMPANIES_HOUSE_KEY", "abn": "ABN_GUID",
            "cloudflare": "CLOUDFLARE_API_TOKEN", "email": "SMTP_PASSWORD"}
# The internal office's mailbox settings (customer offices keep them in their "email" integration's meta)
EMAIL_ENV = {"smtp_host": "SMTP_HOST", "smtp_port": "SMTP_PORT", "username": "SMTP_USERNAME", "from": "OUTREACH_FROM",
             "imap_host": "IMAP_HOST", "imap_port": "IMAP_PORT"}


class Toolbox:
    def __init__(self, integrations: "Integrations", ctx: OrgContext):
        self._i = integrations
        self.ctx = ctx
        self._cache: dict[str, object] = {}

    def _http(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=timeout, transport=self._i.transport,
                            headers={"User-Agent": "WotsOffice/1.0 (lead verification)"})

    def secret(self, kind: str) -> str | None:
        return self._i.secret(self.ctx, kind)

    def places(self) -> PlacesClient:
        if "places" not in self._cache:
            self._cache["places"] = PlacesClient(self.secret("places"), self._http(self._i.settings.request_timeout_seconds))
        return self._cache["places"]

    def companies_house(self) -> CompaniesHouseClient:
        if "ch" not in self._cache:
            self._cache["ch"] = CompaniesHouseClient(self.secret("companies_house"),
                                                     self._http(self._i.settings.request_timeout_seconds))
        return self._cache["ch"]

    def abn(self) -> AbnClient:
        if "abn" not in self._cache:
            self._cache["abn"] = AbnClient(self.secret("abn"), self._http(self._i.settings.request_timeout_seconds))
        return self._cache["abn"]

    def osm(self) -> OsmClient:
        """OpenStreetMap: free, no key."""
        if "osm" not in self._cache:
            s = self._i.settings
            self._cache["osm"] = OsmClient(self._http(s.request_timeout_seconds), s.nominatim_url, s.overpass_urls,
                                          retry_pause=0 if self._i.transport else 5)
        return self._cache["osm"]

    def preview_host(self):
        """Where approved sites are published: nowhere in dry-run mode (a local copy instead)."""
        if "previews" not in self._cache:
            config = self._i.config
            if config.settings.dry_run:
                self._cache["previews"] = LocalPreviewHost(config.settings.data_path / "previews" / self.ctx.org_id)
            else:
                account = self._i.meta(self.ctx, "cloudflare").get("account_id") or (
                    os.environ.get("CLOUDFLARE_ACCOUNT_ID") if self.ctx.is_internal else None)
                self._cache["previews"] = CloudflarePagesHost(
                    self.secret("cloudflare"), account, config.settings.previews.cloudflare_project,
                    self._http(self._i.settings.request_timeout_seconds), uploader=self._i.uploader)
        return self._cache["previews"]

    # ---------------------------------------------------------------- outreach (Phase 4)

    def outreach(self):
        from ..core.outreach import OutreachDesk

        tz = self.ctx.settings.get("timezone", self._i.config.settings.timezone)
        return OutreachDesk(self._i.sessions, self.ctx, self._i.clock, tz)

    def mailbox(self) -> dict:
        """The office's mailbox settings: host, port, username, from address, IMAP host."""
        meta = self._i.meta(self.ctx, "email")
        if self.ctx.is_internal:
            for key, env in EMAIL_ENV.items():
                meta.setdefault(key, os.environ.get(env) or None)
        return meta

    def from_address(self) -> str | None:
        return self.mailbox().get("from")

    def email_sender(self):
        """Dry run: an outbox folder. Otherwise the office's own mailbox over SMTP."""
        from .email import OutboxSender, SmtpSender

        if self._i.email_sender is not None:
            return self._i.email_sender  # tests
        if self._i.config.settings.dry_run:
            return OutboxSender(self._i.config.settings.data_path / "outbox" / self.ctx.org_id / "emails")
        box = self.mailbox()
        return SmtpSender(box.get("smtp_host"), int(box.get("smtp_port") or 587), box.get("username"),
                          self.secret("email"))

    def imap_reader(self):
        """None if the office hasn't set up reading its mailbox (replies are then marked by hand)."""
        from .email import ImapReader

        if self._i.imap_reader is not None:
            return self._i.imap_reader  # tests
        box = self.mailbox()
        password = self.secret("email")
        if not (box.get("imap_host") and box.get("username") and password):
            return None
        return ImapReader(box["imap_host"], int(box.get("imap_port") or 993), box["username"], password)

    def duplicates(self, item) -> list[dict]:
        """Earlier leads in this office that are the same business: same normalised name plus the same
        phone or postcode, or the same source record. The earliest one is kept; later ones are dropped."""
        def postcode_of(p):  # leads not verified yet only have it inside the address
            return normalise_postcode(p.postcode, p.country) or extract_postcode(p.address, p.country)

        name = normalise_name(item.business_name)
        phone = phone_digits(item.phone)
        postcode = postcode_of(item)
        with self._i.sessions() as s:
            rows = s.execute(select(WorkItem, LeadProfile).join(LeadProfile, LeadProfile.work_item_id == WorkItem.id).where(
                WorkItem.org_id == self.ctx.org_id, LeadProfile.org_id == self.ctx.org_id, WorkItem.id != item.id,
                LeadProfile.country == item.country, WorkItem.status != "DISQUALIFIED")).all()
        out = []
        for other, profile in rows:
            if (other.created_at, other.id) > (item.created_at, item.id):
                continue  # the later of two duplicates is the one dropped
            same_source = bool(item.source_ref) and profile.source == item.source and profile.source_ref == item.source_ref
            same_name = normalise_name(profile.business_name) == name
            same_phone = bool(phone) and phone_digits(profile.phone) == phone
            same_postcode = bool(postcode) and postcode_of(profile) == postcode
            if same_source or (same_name and (same_phone or same_postcode)):
                out.append({"id": other.id, "business_name": profile.business_name, "status": other.status,
                            "match": "source" if same_source else "name + " + ("phone" if same_phone else "postcode")})
        return out

    def web(self) -> httpx.Client:
        """For checking whether a business already has a website."""
        if "web" not in self._cache:
            self._cache["web"] = self._http(self._i.settings.website_check_timeout_seconds)
        return self._cache["web"]


class Integrations:
    def __init__(self, sessions, config, transport: httpx.BaseTransport | None = None, uploader=None, clock=None):
        from ..core.clock import utcnow
        from .previews import run_wrangler

        self.sessions = sessions
        self.config = config
        self.transport = transport
        self.uploader = uploader or run_wrangler  # tests replace the Cloudflare upload
        self.clock = clock or utcnow
        self.email_sender = None  # tests replace the mailbox
        self.imap_reader = None

    @property
    def settings(self):
        return self.config.settings.research

    def for_office(self, ctx: OrgContext) -> Toolbox:
        return Toolbox(self, ctx)

    def secret(self, ctx: OrgContext, kind: str) -> str | None:
        with self.sessions() as s:
            row = s.scalars(select(Integration).where(Integration.org_id == ctx.org_id, Integration.kind == kind,
                                                      Integration.status == "connected")).first()
        if row and row.secret_encrypted:
            return decrypt(row.secret_encrypted)
        if ctx.is_internal and kind in ENV_KEYS:
            return os.environ.get(ENV_KEYS[kind]) or None
        return None

    def meta(self, ctx: OrgContext, kind: str) -> dict:
        with self.sessions() as s:
            row = s.scalars(select(Integration).where(Integration.org_id == ctx.org_id, Integration.kind == kind)).first()
        return dict(row.meta or {}) if row else {}

    def connect(self, ctx: OrgContext, kind: str, secret: str, meta: dict | None = None) -> None:
        """Store (or replace) an office's key for an integration, encrypted."""
        with self.sessions.begin() as s:
            row = s.scalars(select(Integration).where(Integration.org_id == ctx.org_id, Integration.kind == kind)).first()
            if row is None:
                row = Integration(org_id=ctx.org_id, kind=kind)
                s.add(row)
            row.secret_encrypted, row.status, row.meta = encrypt(secret), "connected", meta or {}
