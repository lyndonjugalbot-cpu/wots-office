"""Google Places API (New) Text Search (spec v2 §8): the Lead Researcher's official source.

One request returns up to 20 places; a query has at most 3 pages. Each request is billed by
Google, so the caller meters it (usage_events kind=places_call).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .errors import IntegrationConfigError, IntegrationError

URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.addressComponents",
    "places.nationalPhoneNumber", "places.internationalPhoneNumber", "places.websiteUri",
    "places.businessStatus", "places.primaryTypeDisplayName", "places.googleMapsUri", "nextPageToken",
])
REGION_CODES = {"US": "US", "UK": "GB", "AU": "AU"}


@dataclass
class Place:
    id: str
    name: str
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    status: str | None = None  # OPERATIONAL | CLOSED_TEMPORARILY | CLOSED_PERMANENTLY
    category: str | None = None
    maps_url: str | None = None
    components: dict[str, str] = field(default_factory=dict)  # type -> short text (postal_code, ...)
    components_long: dict[str, str] = field(default_factory=dict)

    @property
    def postcode(self) -> str | None:
        return self.components.get("postal_code")

    def region(self, country: str) -> str | None:
        if country == "UK":  # timezones don't vary; the town is the useful bit
            return self.components_long.get("postal_town") or self.components_long.get("administrative_area_level_2")
        return self.components.get("administrative_area_level_1")  # "OR", "VIC"


def parse_place(raw: dict) -> Place:
    short, long_ = {}, {}
    for comp in raw.get("addressComponents") or []:
        for kind in comp.get("types") or []:
            short.setdefault(kind, comp.get("shortText") or comp.get("longText"))
            long_.setdefault(kind, comp.get("longText") or comp.get("shortText"))
    return Place(
        id=raw["id"], name=(raw.get("displayName") or {}).get("text") or "", address=raw.get("formattedAddress"),
        phone=raw.get("nationalPhoneNumber") or raw.get("internationalPhoneNumber"), website=raw.get("websiteUri"),
        status=raw.get("businessStatus"), category=(raw.get("primaryTypeDisplayName") or {}).get("text"),
        maps_url=raw.get("googleMapsUri"), components=short, components_long=long_,
    )


class PlacesClient:
    def __init__(self, api_key: str | None, http: httpx.Client | None = None, timeout: float = 10):
        if not api_key:
            raise IntegrationConfigError("Google Places isn't set up: add GOOGLE_PLACES_KEY to .env")
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=timeout)

    def search(self, query: str, country: str, page_size: int = 20,
               page_token: str | None = None) -> tuple[list[Place], str | None]:
        body = {"textQuery": query, "regionCode": REGION_CODES[country], "pageSize": page_size}
        if page_token:
            body["pageToken"] = page_token
        try:
            res = self.http.post(URL, json=body, headers={"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": FIELDS})
        except httpx.HTTPError as e:
            raise IntegrationError(f"Couldn't reach Google Places: {e}") from e
        if res.status_code in (401, 403):
            raise IntegrationConfigError(f"Google Places refused the key ({res.status_code}): {_message(res)}")
        if res.status_code == 400 and "api key" in _message(res).lower():
            raise IntegrationConfigError(f"Google Places refused the key: {_message(res)}")
        if res.status_code >= 400:
            raise IntegrationError(f"Google Places error {res.status_code}: {_message(res)}")
        data = res.json()
        return [parse_place(p) for p in data.get("places") or []], data.get("nextPageToken")


def _message(res: httpx.Response) -> str:
    try:
        return res.json().get("error", {}).get("message", res.text[:200])
    except ValueError:
        return res.text[:200]
