"""HTML/SVG -> PNG at exact sizes with Playwright (spec v2 §8: the Graphic Designer's renderer)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright


@dataclass
class RenderJob:
    html: str
    width: int
    height: int
    out: Path
    scale: float = 1  # 2 = retina PNG (twice the pixels)


def render_pngs(jobs: list[RenderJob]) -> None:
    """One browser for all the jobs. Pages load no network resources: everything is inline."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for job in jobs:
                page = browser.new_page(viewport={"width": job.width, "height": job.height},
                                        device_scale_factor=job.scale)
                page.route("**/*", lambda route: route.abort() if route.request.url.startswith("http") else route.continue_())
                page.set_content(job.html, wait_until="load")
                job.out.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(job.out), clip={"x": 0, "y": 0, "width": job.width, "height": job.height},
                                omit_background=True)
                page.close()
        finally:
            browser.close()
