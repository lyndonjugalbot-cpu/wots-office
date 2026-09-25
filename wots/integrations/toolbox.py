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
from .places import PlacesClient

ENV_KEYS = {"places": "GOOGLE_PLACES_KEY", "companies_house": "COMPANIES_HOUSE_KEY", "abn": "ABN_GUID"}


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
    def __init__(self, sessions, config, transport: httpx.BaseTransport | None = None):
        self.sessions = sessions
        self.config = config
        self.transport = transport

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

    def connect(self, ctx: OrgContext, kind: str, secret: str, meta: dict | None = None) -> None:
        """Store (or replace) an office's key for an integration, encrypted."""
        with self.sessions.begin() as s:
            row = s.scalars(select(Integration).where(Integration.org_id == ctx.org_id, Integration.kind == kind)).first()
            if row is None:
                row = Integration(org_id=ctx.org_id, kind=kind)
                s.add(row)
            row.secret_encrypted, row.status, row.meta = encrypt(secret), "connected", meta or {}
