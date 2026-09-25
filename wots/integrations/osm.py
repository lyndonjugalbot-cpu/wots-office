"""OpenStreetMap lead source (spec v2 §8, Phase 2): free, and its licence (ODbL) allows reuse with
credit to "© OpenStreetMap contributors".

Nominatim turns a town into a bounding box; Overpass lists businesses in it with the trade's tags
(e.g. craft=plumber). Both are free community servers, so keep use light: one Nominatim lookup
per town, one Overpass query per town, and a daily cap per office.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from .errors import IntegrationError

ATTRIBUTION = "© OpenStreetMap contributors"
COUNTRY_CODES = {"US": "us", "UK": "gb", "AU": "au"}


@dataclass
class Town:
    name: str
    bbox: tuple[float, float, float, float]  # south, west, north, east
    region: str | None  # "OR", "VIC" (from ISO 3166-2), or the town for the UK


@dataclass
class OsmPlace:
    id: str  # "node/123", "way/456"
    name: str
    address: str | None = None
    postcode: str | None = None
    region: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    social: dict[str, str] = field(default_factory=dict)
    category: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


def _first(tags: dict, *keys: str) -> str | None:
    for key in keys:
        value = (tags.get(key) or "").strip()
        if value:
            return value.split(";")[0].strip()  # OSM lists several values with ";"
    return None


def parse_element(element: dict, town: Town, category: str) -> OsmPlace | None:
    tags = element.get("tags") or {}
    name = _first(tags, "name", "brand")
    if not name:
        return None
    street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
    locality = _first(tags, "addr:city", "addr:town", "addr:suburb", "addr:village")
    postcode = _first(tags, "addr:postcode")
    state = _first(tags, "addr:state")
    parts = [p for p in (street, locality, " ".join(x for x in (state, postcode) if x)) if p]
    social = {k: v for k, v in {
        "facebook": _first(tags, "contact:facebook", "facebook"),
        "instagram": _first(tags, "contact:instagram", "instagram"),
    }.items() if v}
    return OsmPlace(
        id=f"{element.get('type', 'node')}/{element.get('id')}", name=name, address=", ".join(parts) or None,
        postcode=postcode, region=state if state and len(state) <= 3 else town.region,
        phone=_first(tags, "phone", "contact:phone", "contact:mobile"),
        email=_first(tags, "email", "contact:email"),
        website=_first(tags, "website", "contact:website", "url"), social=social, category=category, tags=tags,
    )


class OsmClient:
    def __init__(self, http: httpx.Client, nominatim_url: str, overpass_url: str | list[str],
                 retry_pause: float = 5):
        self.http = http
        self.nominatim_url = nominatim_url
        # Public Overpass servers are often busy (429/504): try each in turn, twice
        self.overpass_urls = [overpass_url] if isinstance(overpass_url, str) else list(overpass_url)
        self.retry_pause = retry_pause

    def find_town(self, name: str, country: str) -> Town | None:
        try:
            res = self.http.get(self.nominatim_url, params={
                "q": name, "countrycodes": COUNTRY_CODES[country], "format": "jsonv2", "limit": 1, "addressdetails": 1})
        except httpx.HTTPError as e:
            raise IntegrationError(f"Couldn't reach OpenStreetMap (Nominatim): {e}") from e
        if res.status_code >= 400:
            raise IntegrationError(f"OpenStreetMap (Nominatim) error {res.status_code}")
        found = res.json()
        if not found:
            return None
        hit = found[0]
        south, north, west, east = (float(x) for x in hit["boundingbox"])
        iso = (hit.get("address") or {}).get("ISO3166-2-lvl4", "")  # "US-OR", "AU-VIC", "GB-ENG"
        region = name if country == "UK" else (iso.split("-", 1)[1] if "-" in iso else None)
        return Town(name=name, bbox=(south, west, north, east), region=region)

    def search(self, town: Town, tags: list[tuple[str, str]], category: str, timeout: int = 60) -> list[OsmPlace]:
        s, w, n, e = town.bbox
        union = "".join(f'nwr["{k}"="{v}"]({s},{w},{n},{e});' for k, v in tags)
        query = f"[out:json][timeout:{timeout}];({union});out tags;"
        problem = ""
        for attempt, url in enumerate(self.overpass_urls * 2):
            if attempt:
                time.sleep(self.retry_pause)
            try:
                res = self.http.post(url, data={"data": query}, timeout=timeout + 10)
            except httpx.HTTPError as ex:
                problem = f"couldn't reach it ({ex})"
                continue
            if res.status_code in (429, 502, 503, 504):
                problem = f"busy (HTTP {res.status_code})"
                continue
            if res.status_code >= 400:
                raise IntegrationError(f"OpenStreetMap (Overpass) error {res.status_code}")
            data = res.json()
            if "runtime error" in (data.get("remark") or ""):  # a timeout inside Overpass still returns 200
                problem = data["remark"][:120]
                continue
            places = [parse_element(el, town, category) for el in data.get("elements") or []]
            return [p for p in places if p]
        raise IntegrationError(f"OpenStreetMap (Overpass) is {problem or 'unavailable'}; try again in a few minutes")
