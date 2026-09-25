"""Companies House (UK) company search (spec v2 §8, §11).

UK rules only allow cold email to incorporated bodies (ltd, plc, llp). The Verifier looks the
business up here; a sole trader or partnership isn't on the register, so no match means we don't
contact them. The API is free; the key is the username of HTTP basic auth.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from .errors import IntegrationConfigError, IntegrationError

URL = "https://api.company-information.service.gov.uk/search/companies"
ADVANCED_URL = "https://api.company-information.service.gov.uk/advanced-search/companies"


@dataclass
class Company:
    number: str
    name: str
    status: str | None  # active | dissolved | liquidation ...
    type: str | None  # ltd | plc | llp | private-unlimited | limited-partnership ...
    postcode: str | None
    address: str | None
    locality: str | None = None
    sic_codes: tuple[str, ...] = ()


class CompaniesHouseClient:
    def __init__(self, api_key: str | None, http: httpx.Client | None = None, timeout: float = 10):
        if not api_key:
            raise IntegrationConfigError("Companies House isn't set up: add COMPANIES_HOUSE_KEY to .env "
                                         "(free from developer.company-information.service.gov.uk)")
        self.http = http or httpx.Client(timeout=timeout)
        self.auth = (api_key, "")

    def _get(self, url: str, params: dict) -> httpx.Response:
        try:
            return self.http.get(url, params=params, auth=self.auth)
        except httpx.HTTPError as e:
            raise IntegrationError(f"Couldn't reach Companies House: {e}") from e

    @staticmethod
    def _check(res: httpx.Response) -> None:
        if res.status_code in (401, 403):
            raise IntegrationConfigError(f"Companies House refused the key ({res.status_code})")
        if res.status_code == 429:
            raise IntegrationError("Companies House rate limit reached (600 requests per 5 minutes)")
        if res.status_code >= 400 and res.status_code != 404:
            raise IntegrationError(f"Companies House error {res.status_code}")

    def find_by_trade(self, sic_codes: list[str], location: str, size: int = 100,
                      start: int = 0) -> tuple[list[Company], int]:
        """Active companies with these SIC codes registered in a town (the advanced company search).
        Returns (companies, total hits). A 404 means no hits."""
        res = self._get(ADVANCED_URL, {"sic_codes": ",".join(sic_codes), "location": location,
                                       "company_status": "active", "size": size, "start_index": start})
        self._check(res)
        if res.status_code == 404:
            return [], 0
        data = res.json()
        out = []
        for item in data.get("items") or []:
            addr = item.get("registered_office_address") or {}
            line = ", ".join(p for p in (addr.get("address_line_1"), addr.get("address_line_2"), addr.get("locality"),
                                         addr.get("postal_code")) if p)
            out.append(Company(number=item.get("company_number", ""), name=item.get("company_name", ""),
                               status=item.get("company_status"), type=item.get("company_type"),
                               postcode=addr.get("postal_code"), address=line or None, locality=addr.get("locality"),
                               sic_codes=tuple(item.get("sic_codes") or ())))
        return out, int(data.get("hits") or len(out))

    def search(self, name: str, limit: int = 10) -> list[Company]:
        res = self._get(URL, {"q": name, "items_per_page": limit})
        self._check(res)
        if res.status_code == 404:
            return []
        out = []
        for item in res.json().get("items") or []:
            address = item.get("address") or {}
            out.append(Company(number=item.get("company_number", ""), name=item.get("title", ""),
                               status=item.get("company_status"), type=item.get("company_type"),
                               postcode=address.get("postal_code"), address=item.get("address_snippet")))
        return out
