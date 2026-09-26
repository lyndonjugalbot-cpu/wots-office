"""Pieces of a generated site that come from facts, not from the copywriter (spec v2 §8).

- photos: licensed stock photos for the business's trade (templates/sites/photos/, curated by
  scripts/photos/fetch_pexels.py). Never presented as the business's own work: the site says
  they're illustrative, and so does the pitch.
- hero layout: split / full / centered, picked per business so sites don't all look alike
- trust strip, how-it-works steps and FAQ: built only from what we know (town, phone, email,
  address, the copywriter's services). No reviews, ratings, years in business or other claims.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from markupsafe import Markup

from ...core.config import ROOT

TEMPLATES = ROOT / "templates" / "sites"
PHOTOS = TEMPLATES / "photos"
FONTS = TEMPLATES / "fonts"

_svg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>'
PHONE = Markup(_svg.format('<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/>'))
PIN = Markup(_svg.format('<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0z"/><circle cx="12" cy="10" r="3"/>'))
MAIL = Markup(_svg.format('<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 6-10 7L2 6"/>'))
CHAT = Markup(_svg.format('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'))

# How-it-works steps per business family: what any customer does, not claims about the business
STEPS = {
    "trades": [("Get in touch", "Call or send a message with a few details about the job."),
               ("Talk it through", "We'll go over what you need and the next steps."),
               ("Job done", "The work gets done, and you know who to call next time.")],
    "food": [("Drop in", "Come and see us, or get in touch first."),
             ("Take your pick", "Have a look at what's on offer today."),
             ("Enjoy", "Sit back and enjoy, or take it with you.")],
    "beauty": [("Book", "Call or send a message to find a time that suits you."),
               ("Relax", "Come in, get comfortable and let us take care of the rest."),
               ("Leave refreshed", "Head out feeling better than when you arrived.")],
    "retail": [("Visit", "Pop in to browse, or get in touch first."),
               ("Find what you need", "Ask us anything; we're happy to help you choose."),
               ("Take it home", "Leave with something you'll love.")],
    "professional": [("Get in touch", "Call or email with a short note about what you need."),
                     ("Talk it through", "We'll listen, ask the right questions and suggest a way forward."),
                     ("We take it from there", "You'll know what happens next and when.")],
}


def seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest(), 16)


def theme_fonts(style_css: str) -> list[str]:
    """The font files a theme uses, from its first line ("... Fonts: inter")."""
    m = re.search(r"Fonts:\s*([a-z0-9\- ]+)\*/", style_css.splitlines()[0] if style_css else "")
    return [f for f in (m.group(1).split() if m else []) if (FONTS / f"{f}.woff2").exists()]


def copy_fonts(fonts: list[str], site: Path) -> None:
    if not fonts:
        return
    (site / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    for f in fonts:
        shutil.copy(FONTS / f"{f}.woff2", site / "assets" / "fonts" / f"{f}.woff2")


def photo_topic(category: str | None, family: str) -> dict | None:
    """The photo set for this trade, falling back to the business family's general set."""
    manifest = PHOTOS / "manifest.json"
    if not manifest.exists():
        return None
    topics = json.loads(manifest.read_text()).get("topics", {})
    text = (category or "").lower()
    for topic in topics.values():
        if any(k in text for k in topic.get("keywords", [])) and topic.get("photos"):
            return topic
    general = topics.get(family)
    return general if general and general.get("photos") else None


def pick_photos(lead, family: str, site: Path) -> dict:
    """Copy up to five photos for this business into the site and say where each one goes."""
    topic = photo_topic(lead.category, family)
    out: dict = {"hero": None, "about": None, "gallery": [], "any": False, "credits": []}
    if not topic:
        return out
    photos = list(topic["photos"])
    start = seed(lead.business_name) % len(photos)
    ordered = photos[start:] + photos[:start]  # different businesses lead with different photos
    (site / "assets" / "photos").mkdir(parents=True, exist_ok=True)
    chosen = []
    for i, p in enumerate(ordered[:5]):
        src = PHOTOS / p["file"]
        if not src.exists():
            continue
        name = f"photo-{i + 1}{src.suffix}"
        shutil.copy(src, site / "assets" / "photos" / name)
        chosen.append({"src": f"assets/photos/{name}", "alt": p.get("alt", ""), "source": p.get("source"),
                       "photographer": p.get("photographer")})
    if not chosen:
        return out
    out["hero"] = chosen[0]
    out["about"] = chosen[1] if len(chosen) > 1 else None
    out["gallery"] = chosen[2:5] if len(chosen) >= 5 else []
    out["any"] = True
    out["credits"] = [{"file": c["src"], "source": c["source"], "photographer": c["photographer"]} for c in chosen]
    return out


def hero_layout(business_name: str, style: str, has_photo: bool) -> str:
    if not has_photo:
        return "centered"
    options = {"minimal": ["split", "split", "full"], "playful": ["split", "full"], "bold_warm": ["full", "split"],
               "clean_modern": ["split", "full"]}.get(style, ["split", "full"])
    return options[seed(business_name + style) % len(options)]


def trust_items(lead) -> list[dict]:
    items = []
    where = lead.region or (lead.address.split(",")[-1].strip() if lead.address else None)
    if where:
        items.append({"icon": PIN, "title": f"Based in {where}", "text": "Local and easy to reach"})
    if lead.phone:
        items.append({"icon": PHONE, "title": lead.phone, "text": "Call to talk it through"})
    if lead.email:
        items.append({"icon": MAIL, "title": "Email us", "text": lead.email})
    if len(items) < 3:
        items.append({"icon": CHAT, "title": "Friendly and local", "text": "Get in touch with any questions"})
    return items[:3]


def steps(family: str) -> list[dict]:
    return [{"title": t, "text": x} for t, x in STEPS.get(family, STEPS["professional"])]


def faq(lead, copy: dict) -> list[dict]:
    """Questions answered only with facts on the record."""
    out = []
    ways = [w for w in (f"call {lead.phone}" if lead.phone else "", f"email {lead.email}" if lead.email else "") if w]
    if ways:
        out.append({"question": "How do I get in touch?",
                    "answer": f"The quickest way is to {' or '.join(ways)}."})
    if lead.address:
        out.append({"question": "Where are you based?", "answer": f"You'll find us at {lead.address}."})
    elif lead.region:
        out.append({"question": "Where are you based?", "answer": f"We're based in {lead.region}."})
    names = [s["name"] for s in copy.get("services", []) if s.get("name")]
    if names:
        listed = ", ".join(names[:-1]) + (f" and {names[-1]}" if len(names) > 1 else names[0])
        out.append({"question": "What can you help with?",
                    "answer": f"{listed}. If you need something else, just ask."})
    return out
