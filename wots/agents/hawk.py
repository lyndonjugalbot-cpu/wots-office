"""Hawk: QA for websites (spec §7). Ad checks arrive in Phase 5.

Checks the built site in a real browser at 375, 768 and 1440 px, saves screenshots for the
approval queue, runs Lighthouse and an LLM proofread, and writes qa/qa_report.json.
Any `high` issue fails the site back to its designer (NEEDS_FIX).
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from playwright.sync_api import sync_playwright

from ..core.llm import BudgetExceeded, LLMError
from ..core.models import Lead
from ..integrations.lighthouse import LighthouseError, run_lighthouse
from .base import AgentContext, AgentResult, ArtifactOut, BaseAgent

WIDTHS = (375, 768, 1440)
PLACEHOLDERS = re.compile(r"lorem|\[business name\]|\btodo\b|example\.com", re.IGNORECASE)

PROOFREAD_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "description": {"type": "string"},
                    "where": {"type": "string"},
                    "suggested_fix": {"type": "string"},
                },
                "required": ["severity", "description", "where", "suggested_fix"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["issues"],
    "additionalProperties": False,
}


def issue(severity: str, description: str, where: str, suggested_fix: str) -> dict:
    return {"severity": severity, "description": description, "where": where, "suggested_fix": suggested_fix}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


@contextmanager
def serve(directory: Path):
    """Serve a folder on a free localhost port for the duration of the checks."""
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep Atlas output readable
        pass


class Hawk(BaseAgent):
    def run(self, lead: Lead, ctx: AgentContext) -> AgentResult:
        if lead.scope != "website":
            raise NotImplementedError("Ad QA arrives in Phase 5")
        qa_cfg = ctx.config.settings.qa
        site = ctx.lead_dir / "site"
        qa_dir = ctx.lead_dir / "qa"
        (qa_dir / "screens").mkdir(parents=True, exist_ok=True)
        issues: list[dict] = []
        if not (site / "index.html").exists():
            issues.append(issue("high", "No site/index.html was built", "site/", "Rebuild the site"))
            return self._verdict(lead, ctx, issues, {}, [])

        html = (site / "index.html").read_text()
        with serve(site) as base_url, sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page_text, screenshots = self._browser_checks(browser, base_url, site, lead, issues, qa_dir,
                                                              qa_cfg.check_external_links)
            finally:
                browser.close()
            scores = {}
            if qa_cfg.run_lighthouse:
                scores = self._lighthouse(base_url, pw.chromium.executable_path, qa_cfg.lighthouse, issues)

        self._content_checks(html, page_text, lead, issues)
        self._proofread(page_text, lead, ctx, issues)
        return self._verdict(lead, ctx, issues, scores, screenshots)

    # ------------------------------------------------------------------ checks

    def _browser_checks(self, browser, base_url, site, lead, issues, qa_dir, check_external):
        page_text, screenshots, links, ids = "", [], set(), set()
        for width in WIDTHS:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors: list[str] = []
            page.on("console", lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.on("response", lambda r, errors=errors: errors.append(f"HTTP {r.status} for {r.url}")
                    if r.status >= 400 and r.url.startswith(base_url) else None)
            page.goto(base_url, wait_until="networkidle")
            for err in dict.fromkeys(errors):
                issues.append(issue("high", f"Console error at {width}px: {err[:200]}", f"{width}px", "Fix the error"))
            broken = page.eval_on_selector_all("img", "imgs => imgs.filter(i => !i.complete || i.naturalWidth === 0).map(i => i.getAttribute('src'))")
            for src in broken:
                issues.append(issue("high", f"Image doesn't load: {src}", f"{width}px", "Fix the path or remove the image"))
            if page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
                issues.append(issue("medium", f"Page scrolls sideways at {width}px", f"{width}px", "Let content wrap"))
            shot = qa_dir / "screens" / f"{width}.png"
            page.screenshot(path=str(shot), full_page=True)
            screenshots.append(f"qa/screens/{width}.png")
            if width == WIDTHS[-1]:
                page_text = page.inner_text("body")
                links = set(page.eval_on_selector_all("a[href]", "as => as.map(a => a.getAttribute('href'))"))
                ids = set(page.eval_on_selector_all("[id]", "els => els.map(e => e.id)"))
            page.close()
        self._link_checks(links, ids, site, lead, issues, check_external)
        return page_text, screenshots

    def _link_checks(self, links, ids, site, lead, issues, check_external):
        for href in sorted(links):
            if href.startswith("#"):
                if href[1:] and href[1:] not in ids:
                    issues.append(issue("high", f"Link to missing section {href}", href, "Point it at an existing id"))
            elif href.startswith("tel:"):
                if len(re.sub(r"\D", "", href)) < 7:
                    issues.append(issue("high", f"Phone link looks wrong: {href}", href, "Use the lead's phone number"))
            elif href.startswith("mailto:"):
                if lead.email and unquote(href[7:]).lower() != lead.email.lower():
                    issues.append(issue("high", f"Email link {href} doesn't match {lead.email}", href, "Use the lead's email"))
            elif href.startswith(("http://", "https://")):
                if check_external:
                    self._check_external(href, issues)
            else:
                target = (site / urlparse(href).path).resolve()
                if not target.exists():
                    issues.append(issue("high", f"Broken link: {href}", href, "Fix or remove the link"))

    @staticmethod
    def _check_external(href, issues):
        try:
            r = httpx.head(href, follow_redirects=True, timeout=10)
            if r.status_code >= 400:
                r = httpx.get(href, follow_redirects=True, timeout=10)
            if r.status_code >= 400:
                issues.append(issue("medium", f"External link returns {r.status_code}: {href}", href, "Check the URL"))
        except httpx.HTTPError as e:
            issues.append(issue("medium", f"External link unreachable ({type(e).__name__}): {href}", href, "Check the URL"))

    def _content_checks(self, html, page_text, lead, issues):
        text = _squash(page_text)
        for field in ("phone", "email", "address"):
            value = getattr(lead, field)
            if value and _squash(value) not in text:
                issues.append(issue("high", f"The {field} on the page doesn't match the lead record ({value})",
                                    "Contact section", f"Show the {field} exactly as recorded"))
        for match in sorted({m.group(0).lower() for m in PLACEHOLDERS.finditer(html)}):
            issues.append(issue("high", f"Placeholder text found: “{match}”", "Page source", "Replace it with real content"))
        if not re.search(r'<meta\s+name="robots"\s+content="[^"]*noindex', html, re.IGNORECASE):
            issues.append(issue("high", "The noindex meta tag is missing", "<head>", 'Add <meta name="robots" content="noindex">'))

    @staticmethod
    def _lighthouse(url, chrome_path, thresholds, issues) -> dict:
        try:
            result = run_lighthouse(url, chrome_path)
        except LighthouseError as e:
            issues.append(issue("high", f"Lighthouse couldn't run: {e}", "Lighthouse", "Check the tools install"))
            return {}
        for category, minimum in thresholds.items():
            key = "best-practices" if category == "best_practices" else category
            score = result["scores"].get(key)
            if score is not None and score < minimum:
                failing = ", ".join(result["failing_audits"].get(key, [])[:5])
                issues.append(issue("high", f"Lighthouse {category} {score} is below {minimum}", "Lighthouse",
                                    f"Fix: {failing}" if failing else "See the Lighthouse report"))
        return result

    def _proofread(self, page_text, lead, ctx: AgentContext, issues):
        if not ctx.llm or not ctx.model:
            return
        spelling = ctx.config.countries[lead.country].spelling
        try:
            response = ctx.llm.complete(
                agent=self.name, model=ctx.model, lead_id=lead.id, max_tokens=4000,
                system=(f"You proofread website text for a small business. The locale is {spelling}. "
                        "Report only real problems: spelling or grammar mistakes, spelling that doesn't match the "
                        "locale (for example US spelling on a UK site), or broken sentences. Use severity high only "
                        "for an obvious misspelling in a heading or the business name, medium for other clear "
                        "errors, low for style. Return an empty list if the text is fine."),
                messages=[{"role": "user", "content": page_text[:12000]}],
                output_config={"format": {"type": "json_schema", "schema": PROOFREAD_SCHEMA}},
            )
            text = next((b.text for b in response.content if b.type == "text"), "{}")
            for item in json.loads(text).get("issues", []):
                item["description"] = f"Proofread: {item['description']}"
                issues.append(item)
        except BudgetExceeded:
            raise
        except (LLMError, json.JSONDecodeError) as e:
            issues.append(issue("medium", f"Proofread didn't run: {e}", "LLM", "Proofread manually"))

    # ------------------------------------------------------------------ verdict

    def _verdict(self, lead, ctx, issues, lighthouse, screenshots) -> AgentResult:
        passed = not any(i["severity"] == "high" for i in issues)
        report = {"passed": passed, "issues": issues, "lighthouse": lighthouse, "screenshots": screenshots}
        (ctx.lead_dir / "qa" / "qa_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        highs = sum(i["severity"] == "high" for i in issues)
        scores = lighthouse.get("scores", {}) if lighthouse else {}
        summary = ", ".join(f"{k} {v}" for k, v in scores.items())
        note = (f"QA passed" if passed else f"QA failed: {highs} high issue(s)") + (f"; Lighthouse {summary}" if summary else "")
        return AgentResult("READY_FOR_APPROVAL" if passed else "NEEDS_FIX", note,
                           artifacts=[ArtifactOut("qa_report", "qa/qa_report.json")],
                           qa_report={"passed": passed, "issues": issues})
