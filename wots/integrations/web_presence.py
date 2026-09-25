"""Does the business already have a website? (spec v2 §8)

Social-only presence counts as no website. For the rest we guess likely domains from the name and
check whether any of them serves a page about this business (its name or phone number on the page).
A domain that resolves to someone else, or to a parking page, doesn't count.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ..core.lead_identity import name_tokens, phone_digits

SOCIAL = {
    "facebook": ("facebook.com", "fb.com", "fb.me"), "instagram": ("instagram.com",), "linkedin": ("linkedin.com",),
    "x": ("twitter.com", "x.com"), "tiktok": ("tiktok.com",), "youtube": ("youtube.com", "youtu.be"),
    "yelp": ("yelp.com", "yelp.co.uk", "yelp.com.au"), "google": ("business.site", "g.page", "google.com"),
    "linktree": ("linktr.ee",),
}
TLDS = {"US": ["com", "net", "us"], "UK": ["co.uk", "com", "uk"], "AU": ["com.au", "com", "au"]}
PARKED = ("domain is for sale", "buy this domain", "parked free", "this domain may be for sale", "godaddy",
          "sedo", "dan.com", "hugedomains", "domain parking")


def host(url: str) -> str:
    netloc = urlparse(url if "//" in url else f"//{url}").netloc.lower()
    return netloc.removeprefix("www.")


def social_kind(url: str) -> str | None:
    h = host(url)
    for kind, domains in SOCIAL.items():
        if any(h == d or h.endswith("." + d) for d in domains):
            return kind
    return None


def likely_domains(name: str, country: str) -> list[str]:
    tokens = name_tokens(name)
    if not tokens:
        return []
    stems = ["".join(tokens)]
    if len(tokens) > 1:
        stems.append("-".join(tokens))
    return [f"{stem}.{tld}" for stem in stems for tld in TLDS.get(country, ["com"])]


@dataclass
class DomainCheck:
    domain: str
    status: str  # none | unrelated | parked | match
    evidence: str = ""


def check_domain(http: httpx.Client, domain: str, name: str, phone: str | None) -> DomainCheck:
    try:
        res = http.get(f"https://{domain}", follow_redirects=True)
    except httpx.HTTPError:
        try:
            res = http.get(f"http://{domain}", follow_redirects=True)
        except httpx.HTTPError:
            return DomainCheck(domain, "none")
    if res.status_code >= 400:
        return DomainCheck(domain, "none", f"HTTP {res.status_code}")
    page = re.sub(r"\s+", " ", res.text[:300_000]).lower()
    if any(p in page for p in PARKED):
        return DomainCheck(domain, "parked")
    digits = phone_digits(phone)
    if digits and digits in re.sub(r"\D", "", page):
        return DomainCheck(domain, "match", "phone number on the page")
    tokens = [t for t in name_tokens(name) if len(t) > 2]
    if tokens and all(t in page for t in tokens):
        return DomainCheck(domain, "match", "business name on the page")
    return DomainCheck(domain, "unrelated")
