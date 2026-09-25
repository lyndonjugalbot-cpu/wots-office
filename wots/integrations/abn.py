"""ABN Lookup (Australia) (spec v2 §8): find the business's ABN and drop cancelled ones.

The JSON endpoints reply as JSONP (`callback({...})`); the GUID is free from abr.business.gov.au.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from .errors import IntegrationConfigError, IntegrationError

BASE = "https://abr.business.gov.au/json"


@dataclass
class AbnMatch:
    abn: str
    name: str
    postcode: str | None
    state: str | None
    score: int
    current: bool


@dataclass
class AbnRecord:
    abn: str
    status: str  # Active | Cancelled
    entity_name: str | None
    entity_type: str | None  # e.g. "Individual/Sole Trader", "Australian Private Company"
    postcode: str | None
    state: str | None
    business_names: list[str]


class AbnClient:
    def __init__(self, guid: str | None, http: httpx.Client | None = None, timeout: float = 10):
        if not guid:
            raise IntegrationConfigError("ABN Lookup isn't set up: add ABN_GUID to .env "
                                         "(free from abr.business.gov.au/Tools/WebServices)")
        self.guid = guid
        self.http = http or httpx.Client(timeout=timeout)

    def _get(self, path: str, params: dict) -> dict:
        try:
            res = self.http.get(f"{BASE}/{path}", params={**params, "guid": self.guid})
        except httpx.HTTPError as e:
            raise IntegrationError(f"Couldn't reach ABN Lookup: {e}") from e
        if res.status_code >= 400:
            raise IntegrationError(f"ABN Lookup error {res.status_code}")
        text = res.text.strip()
        m = re.match(r"^[\w$.]*\((.*)\)\s*;?$", text, re.S)
        data = json.loads(m.group(1) if m else text)
        message = (data.get("Message") or "").strip()
        if "guid" in message.lower():
            raise IntegrationConfigError(f"ABN Lookup refused the GUID: {message}")
        return data

    def match(self, name: str, postcode: str | None = None, limit: int = 10) -> list[AbnMatch]:
        params = {"name": name, "maxResults": limit}
        if postcode:
            params["postcode"] = postcode
        data = self._get("MatchingNames.aspx", params)
        return [AbnMatch(abn=n.get("Abn", ""), name=n.get("Name", ""), postcode=n.get("Postcode"),
                         state=n.get("State"), score=int(n.get("Score") or 0), current=bool(n.get("IsCurrent", True)))
                for n in data.get("Names") or []]

    def details(self, abn: str) -> AbnRecord:
        data = self._get("AbnDetails.aspx", {"abn": abn})
        return AbnRecord(abn=data.get("Abn", abn), status=data.get("AbnStatus") or "", entity_name=data.get("EntityName"),
                         entity_type=data.get("EntityTypeName"), postcode=data.get("AddressPostcode"),
                         state=data.get("AddressState"), business_names=list(data.get("BusinessName") or []))
