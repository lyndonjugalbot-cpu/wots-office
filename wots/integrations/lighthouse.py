"""Lighthouse CLI (Node) called from Python (spec §3). Installed in tools/ with `npm install`."""
from __future__ import annotations

import json
import os
import subprocess

from ..core.config import ROOT

LIGHTHOUSE = ROOT / "tools" / "node_modules" / ".bin" / "lighthouse"


class LighthouseError(Exception):
    pass


def run_lighthouse(url: str, chrome_path: str, ignore_audits: frozenset[str] = frozenset({"is-crawlable"})) -> dict:
    """Return category scores (0-100) and the failing audits for performance, accessibility and SEO.

    Audits in `ignore_audits` are left out of the score: the SEO check ignores noindex (spec §7),
    which is deliberately on every preview."""
    if not LIGHTHOUSE.exists():
        raise LighthouseError("Lighthouse isn't installed. Run `npm install` in tools/.")
    cmd = [str(LIGHTHOUSE), url, "--output=json", "--output-path=stdout", "--quiet",
           "--only-categories=performance,accessibility,seo", "--chrome-flags=--headless=new --no-sandbox"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                              env={**os.environ, "CHROME_PATH": chrome_path})
    except subprocess.TimeoutExpired as e:
        raise LighthouseError("Lighthouse timed out after 180s") from e
    if proc.returncode != 0 or not proc.stdout.strip():
        raise LighthouseError(f"Lighthouse failed: {proc.stderr.strip()[-300:]}")
    report = json.loads(proc.stdout)
    audits = report["audits"]
    scores, failing = {}, {}
    for name, category in report["categories"].items():
        total = weight = 0.0
        for ref in category["auditRefs"]:
            score = audits.get(ref["id"], {}).get("score")
            if ref["weight"] <= 0 or score is None or ref["id"] in ignore_audits:
                continue
            total += ref["weight"] * score
            weight += ref["weight"]
            if score < 0.9:
                failing.setdefault(name, []).append(audits[ref["id"]]["title"])
        scores[name] = round(100 * total / weight) if weight else 100
    return {"scores": scores, "failing_audits": failing}
