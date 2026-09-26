"""Graphic Designer (Iris / Juno in the internal office), spec v2 §8. One class; hires differ by config.

Website task (Phase 3): a brand palette, a logo in the designer's style (flat_brand, bold_playful,
minimal), a square mark for the favicon, a decorative hero illustration and a 1200x630 share image,
plus PNG versions for emails. No LLM, and never a fake photo: everything is drawn from the brand
colours, the business's name and a trade icon. Colours are checked for WCAG AA contrast.
Ad variations arrive in Phase 5.
"""
from __future__ import annotations

import hashlib
import json
import random
from html import escape

from ..base import ArtifactOut, BaseEmployee, EmployeeContext, EmployeeResult

# Simple line icons per business family (24x24 SVG paths); the web developer uses them too
ICON_PATHS = {
    "trades": "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-2.5z",
    "food": "M4 3v8a3 3 0 0 0 6 0V3M7 3v18M17 3c-2 2-3 5-3 8h3v10",
    "beauty": "M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5z",
    "retail": "M5 8h14l-1 13H6zM9 8V6a3 3 0 0 1 6 0v2",
    "professional": "M4 7h16v12H4zM9 7V5h6v2M4 12h16",
}
ICONS = {k: f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="{d}"/></svg>'
         for k, d in ICON_PATHS.items()}
STYLES = {  # per designer style_profile: mark shape and type
    "flat_brand": {"shape": "circle", "font": "system-ui, -apple-system, 'Segoe UI', sans-serif", "weight": 700,
                   "spacing": 0},
    "bold_playful": {"shape": "blob", "font": "'Trebuchet MS', 'Avenir Next', Verdana, sans-serif", "weight": 800,
                     "spacing": 0},
    "minimal": {"shape": "square", "font": "'Helvetica Neue', Arial, sans-serif", "weight": 400, "spacing": 2},
}

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


def tint(hex_color: str, amount: float) -> str:
    """Mix a colour with white (amount 0..1 of white)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (round(v + (255 - v) * amount) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _mark_shape(shape: str, primary: str, accent: str, size: int = 56) -> str:
    h = size / 2
    if shape == "blob":
        return (f'<path d="M{h} 2 C{size - 6} 2 {size - 2} {h - 10} {size - 2} {h} C{size - 2} {size - 6} {h + 8} {size - 2} '
                f'{h} {size - 2} C8 {size - 2} 2 {h + 10} 2 {h} C2 8 10 2 {h} 2Z" fill="{primary}"/>'
                f'<circle cx="{size - 10}" cy="10" r="6" fill="{accent}"/>')
    if shape == "square":
        return (f'<rect x="3" y="3" width="{size - 6}" height="{size - 6}" fill="none" stroke="{primary}" stroke-width="3"/>'
                f'<rect x="{size - 14}" y="{size - 14}" width="8" height="8" fill="{accent}"/>')
    return (f'<circle cx="{h}" cy="{h}" r="{h - 2}" fill="{primary}"/>'
            f'<circle cx="{h}" cy="{h}" r="{h - 2}" fill="none" stroke="{accent}" stroke-width="3"/>')


class GraphicDesigner(BaseEmployee):
    type_key = "graphic_designer"

    def run(self, lead, task: str, ctx: EmployeeContext) -> EmployeeResult:
        if task != "website_assets":
            raise NotImplementedError("Ad variations arrive in Phase 5")
        family = family_for(lead.category)
        seed = int(hashlib.sha256(lead.business_name.encode()).hexdigest(), 16)
        primary = darken_until(PALETTES[family][seed % len(PALETTES[family])])
        accent = ACCENTS[(seed >> 8) % len(ACCENTS)]
        style_key = self.config.get("style_profile", "flat_brand")
        style = STYLES.get(style_key, STYLES["flat_brand"])
        brand = {
            "family": family,
            "primary": primary,
            "primary_dark": darken_until(primary, ratio=8),
            "accent": accent,
            "ink": "#1c1917",
            "paper": "#fffdf8",
            "contrast_on_primary": round(contrast(primary, "#ffffff"), 2),
            "style_profile": style_key,
        }
        assets = ctx.item_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "brand.json").write_text(json.dumps(brand, indent=2))
        (assets / "logo.svg").write_text(self._logo(lead.business_name, primary, accent, style))
        (assets / "mark.svg").write_text(self._mark(lead.business_name, primary, accent, style))
        (assets / "hero.svg").write_text(self._hero(family, primary, accent, seed))
        copy_path = ctx.item_dir / "copy.json"
        headline = json.loads(copy_path.read_text()).get("headline") if copy_path.exists() else None
        pngs = self._render(assets, lead.business_name, headline, brand, style)
        manifest = {
            "style_profile": style_key, "palette": brand,
            "logo": "assets/logo.svg", "mark": "assets/mark.svg", "hero": "assets/hero.svg", **pngs,
            "photos": [],  # only the business's real photos may be used; none were supplied
            "notes": "Drawn from the brand colours and a trade icon; no photos or people were generated.",
        }
        (ctx.item_dir / "design_manifest.json").write_text(json.dumps(manifest, indent=2))
        return EmployeeResult(
            "ASSETS_READY",
            f"Logo ({style_key.replace('_', ' ')}), hero illustration and share image; {family} palette "
            f"{primary} (AA contrast {brand['contrast_on_primary']}:1)",
            artifacts=[ArtifactOut("logo", "assets/logo.svg"), ArtifactOut("hero", "assets/hero.svg")],
        )

    @staticmethod
    def _logo(name: str, primary: str, accent: str, style: dict) -> str:
        label = escape(name)
        mono = escape(initials(name))
        width = 64 + (11 + style["spacing"]) * len(name)
        mono_fill = primary if style["shape"] == "square" else "#ffffff"
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="56" viewBox="0 0 {width} 56" role="img" aria-label="{label} logo">
  {_mark_shape(style["shape"], primary, accent)}
  <text x="28" y="35" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="20" font-weight="700" fill="{mono_fill}">{mono}</text>
  <text x="64" y="35" font-family="{style["font"]}" font-size="19" font-weight="{style["weight"]}" letter-spacing="{style["spacing"]}" fill="{primary}">{label}</text>
</svg>
"""

    @staticmethod
    def _mark(name: str, primary: str, accent: str, style: dict) -> str:
        mono = escape(initials(name))
        mono_fill = primary if style["shape"] == "square" else "#ffffff"
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56" viewBox="0 0 56 56" role="img" aria-label="{escape(name)}">
  {_mark_shape(style["shape"], primary, accent)}
  <text x="28" y="35" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="20" font-weight="700" fill="{mono_fill}">{mono}</text>
</svg>
"""

    @staticmethod
    def _hero(family: str, primary: str, accent: str, seed: int) -> str:
        """A decorative illustration: soft brand shapes, a dot grid and the trade's icon. No text, no photos."""
        rng = random.Random(seed)
        soft, softer = tint(primary, 0.82), tint(primary, 0.92)
        blobs = "".join(
            f'<circle cx="{rng.randint(80, 520)}" cy="{rng.randint(60, 390)}" r="{rng.randint(60, 150)}" '
            f'fill="{rng.choice([soft, tint(accent, 0.6)])}" opacity="{rng.choice([0.55, 0.7, 0.85])}"/>'
            for _ in range(3))
        dots = "".join(f'<circle cx="{x}" cy="{y}" r="3" fill="{primary}" opacity="0.18"/>'
                       for x in range(40, 600, 40) for y in range(40, 450, 40) if rng.random() < 0.35)
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="450" viewBox="0 0 600 450" aria-hidden="true">
  <defs><clipPath id="frame"><rect width="600" height="450" rx="28"/></clipPath></defs>
  <g clip-path="url(#frame)">
  <rect width="600" height="450" fill="{softer}"/>
  {blobs}
  {dots}
  </g>
  <g transform="translate(180 105) scale(10)" fill="none" stroke="{primary}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="{ICON_PATHS[family]}"/>
  </g>
  <rect x="60" y="360" width="140" height="10" rx="5" fill="{accent}"/>
</svg>
"""

    @staticmethod
    def _render(assets, name: str, headline: str | None, brand: dict, style: dict) -> dict:
        """PNG versions for places that can't show SVG: emails, chat previews, social cards."""
        from .render import RenderJob, render_pngs

        logo = (assets / "logo.svg").read_text()
        width = int(logo.split('width="', 1)[1].split('"', 1)[0])
        page = "<!doctype html><html><head><meta charset='utf-8'><style>html,body{{margin:0;background:transparent}}</style></head><body>{}</body></html>"
        og = f"""<!doctype html><html><head><meta charset="utf-8"><style>
  body{{margin:0;width:1200px;height:630px;background:{brand['primary']};color:#fff;font-family:{style['font']};
       display:flex;flex-direction:column;justify-content:center;padding:0 90px;box-sizing:border-box}}
  .mark{{width:112px;height:112px;background:#fff;border-radius:{'0' if style['shape'] == 'square' else '50%'};display:flex;
        align-items:center;justify-content:center;margin-bottom:40px}}
  h1{{font-size:64px;line-height:1.1;margin:0 0 20px;font-weight:{max(style['weight'], 700)}}}
  p{{font-size:34px;margin:0;opacity:.92}}
  .bar{{width:160px;height:12px;border-radius:6px;background:{brand['accent']};margin-top:44px}}
</style></head><body><div class="mark">{(assets / 'mark.svg').read_text().replace('width="56" height="56"', 'width="96" height="96"')}</div>
<h1>{escape(name)}</h1>{f'<p>{escape(headline)}</p>' if headline and headline != name else ''}<div class="bar"></div></body></html>"""
        render_pngs([
            RenderJob(page.format(logo), width, 56, assets / "logo.png", scale=2),
            RenderJob(page.format((assets / "hero.svg").read_text()), 600, 450, assets / "hero.png", scale=2),
            RenderJob(og, 1200, 630, assets / "og.png"),
        ])
        return {"logo_png": "assets/logo.png", "hero_png": "assets/hero.png", "og_image": "assets/og.png"}
