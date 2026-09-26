"""A fake internet for tests: OpenStreetMap (Nominatim + Overpass), Companies House, ABN Lookup,
Google Places and business websites, behind one httpx transport. Nothing reaches the real network.

By default every business is a registered, active company and nobody has a website; tests add
sole traders, cancelled ABNs, websites and Places results as they need them.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx

from wots.core.lead_identity import normalise_name


def place(pid: str, name: str, *, country: str = "US", website: str | None = None, status: str = "OPERATIONAL",
          phone: str | None = None, postcode: str | None = None, region: str | None = None,
          category: str = "Plumber") -> dict:
    code = {"US": "US", "UK": "GB", "AU": "AU"}[country]
    postcode = postcode or {"US": "97201", "UK": "M1 1AE", "AU": "3000"}[country]
    region = region or {"US": "OR", "UK": "Manchester", "AU": "VIC"}[country]
    components = [{"longText": postcode, "shortText": postcode, "types": ["postal_code"]},
                  {"longText": code, "shortText": code, "types": ["country", "political"]}]
    kind = "postal_town" if country == "UK" else "administrative_area_level_1"
    components.append({"longText": region, "shortText": region, "types": [kind, "political"]})
    raw = {"id": pid, "displayName": {"text": name}, "formattedAddress": f"1 Main St, {region} {postcode}",
           "addressComponents": components, "businessStatus": status,
           "nationalPhoneNumber": phone or f"555 01{abs(hash(pid)) % 100:02d}",
           "primaryTypeDisplayName": {"text": category}}
    if website:
        raw["websiteUri"] = website
    return raw


def osm_element(eid: int, name: str, trade: tuple[str, str] = ("craft", "plumber"), **tags) -> dict:
    """An OpenStreetMap node. Extra tags use _ for : (addr_postcode -> addr:postcode)."""
    return {"type": "node", "id": eid,
            "tags": {"name": name, trade[0]: trade[1], **{k.replace("_", ":"): v for k, v in tags.items()}}}


def ch_company(number: str, name: str, town: str, *, company_type: str = "ltd", postcode: str = "M1 1AE") -> dict:
    return {"company_number": number, "company_name": name, "company_status": "active", "company_type": company_type,
            "registered_office_address": {"address_line_1": "1 High St", "locality": town, "postal_code": postcode},
            "sic_codes": ["43220"]}


ISO = {"us": "US-OR", "gb": "GB-ENG", "au": "AU-VIC"}


class FakeWeb:
    def __init__(self):
        self.places: dict[str, list[dict]] = {}  # textQuery -> every result (served 20 per page)
        self.sole_traders: set[str] = set()  # normalised names Companies House doesn't know
        self.companies: dict[str, dict] = {}  # normalised name -> Companies House item overrides
        self.abn: dict[str, dict] = {}  # normalised name -> {"status": ..., "type": ...} (None = no match)
        self.websites: dict[str, str] = {}  # domain -> HTML
        self.osm: dict[str, list[dict]] = {}  # town (lower case) -> OpenStreetMap elements
        self.unknown_towns: set[str] = set()  # towns Nominatim can't find
        self.ch_by_trade: dict[tuple[str, str], list[dict]] = {}  # (sic code, town lower case) -> companies
        self._bbox_town: dict[str, str] = {}
        self.overpass_busy = 0  # how many Overpass requests answer 504 before one works
        self.site_headers: dict[str, dict] = {}  # host -> response headers for self.websites
        self.pages_projects: dict[str, str] = {}  # Cloudflare Pages project -> subdomain
        self.pages_deployments: list[dict] = []  # {"id", "project", "branch"}
        self.pages_taken = False  # the project name is taken on pages.dev (Cloudflare adds a suffix)
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self.handle)

    def hosts(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "places.googleapis.com":
            return self._places(request)
        if host == "api.company-information.service.gov.uk":
            return self._companies_house(request)
        if host == "abr.business.gov.au":
            return self._abn(request)
        if host == "nominatim.openstreetmap.org":
            return self._nominatim(request)
        if host in {"overpass-api.de", "overpass.private.coffee"}:
            if self.overpass_busy:
                self.overpass_busy -= 1
                return httpx.Response(504, text="Gateway Timeout")
            return self._overpass(request)
        if host == "api.cloudflare.com":
            return self._cloudflare(request)
        domain = host.removeprefix("www.")
        if domain in self.websites:
            return httpx.Response(200, text=self.websites[domain], headers=self.site_headers.get(domain, {}))
        raise httpx.ConnectError("no such host", request=request)

    def _places(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        results = self.places.get(body["textQuery"], [])
        start = int(body.get("pageToken") or 0)
        size = body.get("pageSize", 20)
        page = {"places": results[start:start + size]}
        if start + size < len(results):
            page["nextPageToken"] = str(start + size)
        return httpx.Response(200, json=page)

    def _nominatim(self, request: httpx.Request) -> httpx.Response:
        town = request.url.params["q"].strip().lower()
        if town in self.unknown_towns:
            return httpx.Response(200, json=[])
        n = len(self._bbox_town) + 1
        bbox = [f"{n}.0", f"{n}.5", f"-{n}.5", f"-{n}.0"]  # south, north, west, east (Nominatim's order)
        self._bbox_town[f"{n}.0,-{n}.5,{n}.5,-{n}.0"] = town
        cc = request.url.params["countrycodes"]
        return httpx.Response(200, json=[{"boundingbox": bbox, "display_name": town,
                                          "address": {"ISO3166-2-lvl4": ISO[cc]}}])

    def _overpass(self, request: httpx.Request) -> httpx.Response:
        import re
        from urllib.parse import unquote_plus

        query = unquote_plus(request.content.decode()).removeprefix("data=")
        box = re.search(r"\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\)", query)
        key = ",".join(str(float(x)) for x in box.groups())
        town = self._bbox_town.get(key, "")
        wanted = re.findall(r'\["([^"]+)"="([^"]+)"\]', query)
        elements = [e for e in self.osm.get(town, []) if any(e["tags"].get(k) == v for k, v in wanted)]
        return httpx.Response(200, json={"elements": elements})

    def _companies_house(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/advanced-search"):
            p = request.url.params
            town = p["location"].strip().lower()
            items = [c for sic in p["sic_codes"].split(",") for c in self.ch_by_trade.get((sic, town), [])]
            if not items:
                return httpx.Response(404, json={})
            start, size = int(p.get("start_index", 0)), int(p.get("size", 20))
            return httpx.Response(200, json={"items": items[start:start + size], "hits": len(items)})
        name = request.url.params["q"]
        key = normalise_name(name)
        if key in self.sole_traders:
            return httpx.Response(200, json={"items": []})
        item = {"title": f"{name.upper()} LTD", "company_number": f"{abs(hash(key)) % 10**8:08d}",
                "company_status": "active", "company_type": "ltd", "address": {"postal_code": None},
                "address_snippet": "Somewhere, UK"}
        item.update(self.companies.get(key, {}))
        return httpx.Response(200, json={"items": [item]})

    def _cloudflare(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer test-cloudflare-token":
            return httpx.Response(403, json={"success": False, "errors": [{"message": "Authentication error"}]})
        parts = request.url.path.split("/")  # /client/v4/accounts/{acc}/pages/projects[/{name}[/deployments[/{id}]]]
        rest = parts[parts.index("projects") + 1:]
        if request.method == "POST" and not rest:
            name = json.loads(request.content)["name"]
            self.pages_projects[name] = f"{name}{'-7xq' if self.pages_taken else ''}.pages.dev"
            return httpx.Response(200, json={"success": True, "result": {"name": name, "subdomain": self.pages_projects[name]}})
        name = rest[0]
        if name not in self.pages_projects:
            return httpx.Response(404, json={"success": False, "errors": [{"code": 8000007, "message": "Project not found"}]})
        if len(rest) == 1:
            return httpx.Response(200, json={"success": True, "result": {"name": name, "subdomain": self.pages_projects[name]}})
        if request.method == "GET":
            result = [{"id": d["id"], "deployment_trigger": {"metadata": {"branch": d["branch"]}}}
                      for d in self.pages_deployments if d["project"] == name]
            return httpx.Response(200, json={"success": True, "result": result})
        self.pages_deployments = [d for d in self.pages_deployments if d["id"] != rest[2]]
        return httpx.Response(200, json={"success": True, "result": None})

    def upload(self, site, project: str, branch: str, token: str, account_id: str) -> str:
        """Stands in for `wrangler pages deploy`: serves the folder at its branch URL."""
        subdomain = self.pages_projects[project]
        dep = {"id": f"dep{len(self.pages_deployments) + 1}", "project": project, "branch": branch}
        self.pages_deployments.append(dep)
        host = f"{branch}.{subdomain}"
        self.websites[host] = (site / "index.html").read_text()
        headers = (site / "_headers").read_text() if (site / "_headers").exists() else ""
        self.site_headers[host] = {"x-robots-tag": "noindex, nofollow"} if "X-Robots-Tag: noindex" in headers else {}
        return (f"Uploading... (3/3)\n✨ Deployment complete! Take a peek over at https://{dep['id']}.{subdomain}\n"
                f"✨ Deployment alias URL: https://{host}\n")

    def _abn(self, request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        if request.url.path.endswith("MatchingNames.aspx"):
            name = params["name"][0]
            entry = self.abn.get(normalise_name(name), {})
            names = [] if entry is None else [{"Abn": entry.get("abn", "51824753556"), "Name": name, "Score": 100,
                                               "Postcode": params.get("postcode", [None])[0], "State": "VIC",
                                               "IsCurrent": True}]
            return httpx.Response(200, text=f"callback({json.dumps({'Message': '', 'Names': names})})")
        abn = params["abn"][0]
        entry = next((e for e in self.abn.values() if e and e.get("abn") == abn), {})
        data = {"Abn": abn, "AbnStatus": entry.get("status", "Active"), "EntityName": "TEST PTY LTD",
                "EntityTypeName": entry.get("type", "Australian Private Company"), "AddressPostcode": "3000",
                "AddressState": "VIC", "BusinessName": [], "Message": ""}
        return httpx.Response(200, text=f"callback({json.dumps(data)})")
