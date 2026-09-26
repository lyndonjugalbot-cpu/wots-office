"""Preview hosting for approved sites (spec v2 §8, Phase 3).

Cloudflare Pages: its free plan allows commercial use (Vercel's free Hobby plan doesn't). All
previews share one Pages project; each site is its own branch, so it gets a stable URL
https://{alias}.{project}.pages.dev without using up the 100-projects-per-account limit.
Uploads use Cloudflare's own CLI (wrangler, in tools/); everything else uses the REST API.

Every preview is noindexed three ways: the page's robots meta, an X-Robots-Tag header (_headers
file) and robots.txt. Dock checks the live URL before calling it deployed.

In dry-run mode nothing is published: LocalPreviewHost keeps a copy under data/previews/.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from ..core.config import ROOT
from ..core.lead_identity import name_tokens
from .errors import IntegrationConfigError, IntegrationError

API = "https://api.cloudflare.com/client/v4"
HEADERS_FILE = "/*\n  X-Robots-Tag: noindex, nofollow\n  Referrer-Policy: no-referrer\n"
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


@dataclass
class Preview:
    host: str  # cloudflare_pages | local
    alias: str
    url: str  # the stable URL to share
    deployment_url: str | None = None
    live: bool = False  # published on the internet (False in dry-run mode)


def branch_alias(business_name: str, item_id: str) -> str:
    """A branch name Cloudflare keeps whole in the URL (it truncates aliases at 28 characters)."""
    stem = "-".join(name_tokens(business_name))[:21].strip("-") or "site"
    return f"{stem}-{item_id.replace('-', '')[:6]}"


def prepare_site(site: Path) -> None:
    """Belt and braces on top of the page's robots meta: an X-Robots-Tag header and robots.txt."""
    (site / "_headers").write_text(HEADERS_FILE)
    (site / "robots.txt").write_text(ROBOTS_TXT)


def check_noindex(http: httpx.Client, url: str, attempts: int = 5, pause: float = 3) -> dict:
    """Load the live preview. Returns what was found; raises if it isn't up or isn't noindexed."""
    problem = ""
    for attempt in range(attempts):
        if attempt:
            time.sleep(pause)  # a new deployment can take a few seconds to reach the edge
        try:
            res = http.get(url, follow_redirects=True)
        except httpx.HTTPError as e:
            problem = f"couldn't load it ({e})"
            continue
        if res.status_code != 200:
            problem = f"HTTP {res.status_code}"
            continue
        header = "noindex" in res.headers.get("x-robots-tag", "").lower()
        meta = bool(re.search(r'<meta[^>]+name="robots"[^>]+noindex', res.text, re.I))
        if not (header or meta):
            raise IntegrationError(f"The preview at {url} is live but NOT noindexed")
        return {"status": 200, "x_robots_tag": header, "robots_meta": meta}
    raise IntegrationError(f"The preview at {url} isn't working: {problem}")


class LocalPreviewHost:
    """Dry run: keep the preview on this machine; nothing is published."""
    name = "local"

    def __init__(self, root: Path):
        self.root = root

    def url_for(self, alias: str) -> str | None:
        return None  # nothing public

    def deploy(self, site: Path, alias: str) -> Preview:
        target = self.root / alias
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(site, target)
        return Preview(host=self.name, alias=alias, url=f"local:{alias}", live=False)

    def delete(self, alias: str) -> int:
        target = self.root / alias
        if target.exists():
            shutil.rmtree(target)
            return 1
        return 0


def run_wrangler(site: Path, project: str, branch: str, token: str, account_id: str) -> str:
    """Upload a folder as a Pages deployment on `branch`. Returns wrangler's output."""
    binary = ROOT / "tools" / "node_modules" / ".bin" / "wrangler"
    if not binary.exists():
        raise IntegrationConfigError("Cloudflare's CLI isn't installed: run `cd tools && npm install`")
    env = {**os.environ, "CLOUDFLARE_API_TOKEN": token, "CLOUDFLARE_ACCOUNT_ID": account_id,
           "WRANGLER_SEND_METRICS": "false", "CI": "true"}
    result = subprocess.run([str(binary), "pages", "deploy", str(site), "--project-name", project, "--branch", branch,
                             "--commit-dirty=true"], capture_output=True, text=True, env=env, timeout=300)
    output = result.stdout + result.stderr
    if result.returncode != 0:
        if "Authentication error" in output or "10000" in output:
            raise IntegrationConfigError("Cloudflare refused the API token (it needs Pages: Edit)")
        raise IntegrationError(f"Cloudflare upload failed: {output.strip()[-400:]}")
    return output


class CloudflarePagesHost:
    name = "cloudflare_pages"

    def __init__(self, token: str | None, account_id: str | None, project: str, http: httpx.Client,
                 uploader: Callable[..., str] = run_wrangler):
        if not token or not account_id:
            raise IntegrationConfigError("Cloudflare Pages isn't set up: add CLOUDFLARE_API_TOKEN and "
                                         "CLOUDFLARE_ACCOUNT_ID to .env (free account at dash.cloudflare.com)")
        self.token, self.account_id, self.project = token, account_id, project
        self.http = http
        self.uploader = uploader
        self._subdomain: str | None = None

    def _api(self, method: str, path: str, **kw) -> dict:
        try:
            res = self.http.request(method, f"{API}/accounts/{self.account_id}{path}",
                                    headers={"Authorization": f"Bearer {self.token}"}, **kw)
        except httpx.HTTPError as e:
            raise IntegrationError(f"Couldn't reach Cloudflare: {e}") from e
        if res.status_code in (401, 403):
            raise IntegrationConfigError(f"Cloudflare refused the API token ({res.status_code}); it needs Pages: Edit")
        data = res.json() if res.content else {}
        if res.status_code == 404:
            return {"success": False, "not_found": True, **data}
        if res.status_code >= 400 or not data.get("success", False):
            errors = "; ".join(e.get("message", "") for e in data.get("errors") or []) or f"HTTP {res.status_code}"
            raise IntegrationError(f"Cloudflare error: {errors}")
        return data

    def subdomain(self) -> str:
        """The project's pages.dev host, creating the project the first time. (If the name is taken
        on pages.dev, Cloudflare picks a different subdomain, so it's read back, not assumed.)"""
        if self._subdomain:
            return self._subdomain
        found = self._api("GET", f"/pages/projects/{self.project}")
        if found.get("not_found"):
            found = self._api("POST", "/pages/projects", json={"name": self.project, "production_branch": "main"})
        self._subdomain = found["result"]["subdomain"]
        return self._subdomain

    def url_for(self, alias: str) -> str:
        return f"https://{alias}.{self.subdomain()}"

    def deploy(self, site: Path, alias: str) -> Preview:
        subdomain = self.subdomain()
        output = self.uploader(site, self.project, alias, self.token, self.account_id)
        urls = re.findall(r"https://[a-z0-9-]+\." + re.escape(subdomain), output)
        return Preview(host=self.name, alias=alias, url=f"https://{alias}.{subdomain}",
                       deployment_url=next((u for u in urls if not u.startswith(f"https://{alias}.")), None),
                       live=True)

    def delete(self, alias: str) -> int:
        """Remove every deployment on this site's branch. Returns how many were deleted."""
        found = self._api("GET", f"/pages/projects/{self.project}/deployments", params={"env": "preview"})
        deleted = 0
        for dep in found.get("result") or []:
            branch = ((dep.get("deployment_trigger") or {}).get("metadata") or {}).get("branch")
            if branch == alias:
                self._api("DELETE", f"/pages/projects/{self.project}/deployments/{dep['id']}", params={"force": "true"})
                deleted += 1
        return deleted
