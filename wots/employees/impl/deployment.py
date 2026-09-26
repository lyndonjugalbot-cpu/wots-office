"""Deployment Specialist (Dock in the internal office), spec v2 §8, Phase 3.

Publishes an approved site as a private, noindexed preview on the office's preview host
(Cloudflare Pages; see wots/integrations/previews.py), checks the live URL really loads and is
noindexed, and records it on the lead. Previews of LOST leads are taken down by the preview
timer after `previews.preview_ttl_days`.

Dry-run mode publishes nothing: the site is kept locally and the lead gets no preview URL, so
nothing downstream can ever send a link that doesn't work.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone

from ...integrations.previews import branch_alias, check_noindex, prepare_site
from ..base import BaseEmployee, EmployeeContext, EmployeeResult


class Deployment(BaseEmployee):
    type_key = "deployment"

    def run(self, lead, task: str, ctx: EmployeeContext) -> EmployeeResult:
        site = ctx.item_dir / "site"
        if not (site / "index.html").exists():
            raise RuntimeError("There's no built site to deploy")
        page = (site / "index.html").read_text()
        if not re.search(r'<meta[^>]+name="robots"[^>]+noindex', page, re.I):
            raise RuntimeError("The site has no robots noindex tag; previews must never be indexed")

        host = ctx.tools.preview_host()
        alias = branch_alias(lead.business_name, lead.id)
        stage = ctx.item_dir / "publish"  # the QA'd site stays exactly as it was checked
        if stage.exists():
            shutil.rmtree(stage)
        shutil.copytree(site, stage)
        prepare_site(stage)
        url = host.url_for(alias)
        if url:  # link previews (email, chat apps) need absolute image URLs
            html = (stage / "index.html").read_text()
            html = html.replace('content="assets/', f'content="{url}/assets/')
            (stage / "index.html").write_text(html)

        preview = host.deploy(stage, alias)
        record = {"host": preview.host, "alias": alias, "url": preview.url, "deployment_url": preview.deployment_url,
                  "live": preview.live,
                  "deployed_at": (ctx.now or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(timespec="seconds")}
        if preview.live:
            record["noindex"] = check_noindex(ctx.tools.web(), preview.url)
            note = f"Preview live at {preview.url} (noindexed; checked)"
        else:
            note = "Dry run: preview kept locally, not published"
        checks = {**(lead.checks or {}), "preview": record}
        return EmployeeResult("PREVIEW_DEPLOYED", note,
                              updates={"preview_url": preview.url if preview.live else None, "checks": checks})
