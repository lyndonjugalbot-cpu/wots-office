"""Iris / Juno: graphic designers (spec §7). One class; instances differ by config.

Website scope (Phase 1 version): a simple text-based logo and a brand palette, with no LLM.
Colours are checked for WCAG AA contrast before they're handed to the web designers.
Phase 3 adds real logo/hero design; Phase 5 adds ad variations.
"""
from __future__ import annotations

import hashlib
import json
from html import escape

from ..core.models import Lead
from .base import AgentContext, AgentResult, ArtifactOut, BaseAgent

KEYWORDS = {
    # Checked in order, so "bookkeeping" is professional before "book" can make it retail
    "professional": "bookkeep account law legal solicitor consult clinic dental tutor agency insurance finance tax payroll".split(),
    "trades": "plumb electric build roof clean garden landscap carpent paint mechanic locksmith hvac trade handyman".split(),
    "food": "cafe café restaurant bakery food coffee catering bar pizza deli kitchen takeaway".split(),
    "beauty": "salon beauty nail spa barber hair massage wellness yoga fitness gym lash brow".split(),
    "retail": "shop store boutique florist flower retail gift book clothing pet".split(),
}
# Base colours per family; one is picked per business and darkened until white text passes AA
PALETTES = {
    "trades": ["#1f5fa8", "#c2410c", "#166534", "#334155"],
    "food": ["#9a3412", "#7c2d12", "#b45309", "#6d28d9"],
    "beauty": ["#86198f", "#0f766e", "#9d174d", "#6b21a8"],
    "retail": ["#15803d", "#0e7490", "#a21caf", "#b91c1c"],
    "professional": ["#1e3a8a", "#0f172a", "#115e59", "#3f3f46"],
}
ACCENTS = ["#f59e0b", "#fbbf24", "#34d399", "#f472b6", "#60a5fa"]


def family_for(category: str | None) -> str:
    text = (category or "").lower()
    for family, words in KEYWORDS.items():
        if any(w in text for w in words):
            return family
    return "professional"


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def darken_until(hex_color: str, against: str = "#ffffff", ratio: float = 4.5) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    color = hex_color
    while contrast(color, against) < ratio and (r or g or b):
        r, g, b = (int(v * 0.9) for v in (r, g, b))
        color = f"#{r:02x}{g:02x}{b:02x}"
    return color


def initials(name: str) -> str:
    words = [w for w in name.replace("&", " ").split() if w[:1].isalnum() and w.lower() not in {"the", "and", "of"}]
    return "".join(w[0] for w in words[:2]).upper() or name[:1].upper()


class GraphicDesigner(BaseAgent):
    def run(self, lead: Lead, ctx: AgentContext) -> AgentResult:
        if lead.scope != "website":
            raise NotImplementedError("Ad variations arrive in Phase 5")
        family = family_for(lead.category)
        seed = int(hashlib.sha256(lead.business_name.encode()).hexdigest(), 16)
        primary = darken_until(PALETTES[family][seed % len(PALETTES[family])])
        accent = ACCENTS[(seed >> 8) % len(ACCENTS)]
        brand = {
            "family": family,
            "primary": primary,
            "primary_dark": darken_until(primary, ratio=8),
            "accent": accent,
            "ink": "#1c1917",
            "paper": "#fffdf8",
            "contrast_on_primary": round(contrast(primary, "#ffffff"), 2),
        }
        assets = ctx.lead_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "brand.json").write_text(json.dumps(brand, indent=2))
        (assets / "logo.svg").write_text(self._logo(lead.business_name, primary, accent))
        return AgentResult(
            "ASSETS_READY", f"Text logo and {family} palette ({primary}, AA contrast {brand['contrast_on_primary']}:1)",
            artifacts=[ArtifactOut("logo", "assets/logo.svg")],
        )

    @staticmethod
    def _logo(name: str, primary: str, accent: str) -> str:
        label = escape(name)
        mono = escape(initials(name))
        width = 64 + 11 * len(name)
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="56" viewBox="0 0 {width} 56" role="img" aria-label="{label} logo">
  <circle cx="28" cy="28" r="26" fill="{primary}"/>
  <circle cx="28" cy="28" r="26" fill="none" stroke="{accent}" stroke-width="3"/>
  <text x="28" y="35" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="20" font-weight="700" fill="#ffffff">{mono}</text>
  <text x="64" y="35" font-family="system-ui, -apple-system, 'Segoe UI', sans-serif" font-size="19" font-weight="700" fill="{primary}">{label}</text>
</svg>
"""
