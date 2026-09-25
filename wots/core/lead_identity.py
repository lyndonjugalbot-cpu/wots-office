"""Telling businesses apart (spec v2 §8): normalised names, phone digits and postcodes, used by the
Verifier's dedupe and registry matching and by the Lead Researcher's likely-domain guesses."""
from __future__ import annotations

import re
import unicodedata

# Words that don't identify a business: legal suffixes and filler
LEGAL = {"ltd", "limited", "plc", "llp", "llc", "inc", "incorporated", "co", "company", "corp", "corporation",
         "pty", "proprietary", "t/a", "trading", "as"}
FILLER = {"the", "and", "of", "&"}

POSTCODE = {
    "UK": re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I),
    "US": re.compile(r"\b(\d{5})(?:-\d{4})?\b"),
    "AU": re.compile(r"\b(\d{4})\b"),
}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def name_tokens(name: str, *, keep_legal: bool = False) -> list[str]:
    words = re.findall(r"[a-z0-9]+", _ascii(name).lower().replace("'", ""))
    drop = FILLER | (set() if keep_legal else LEGAL)
    return [w for w in words if w not in drop]


def normalise_name(name: str) -> str:
    """"The Copper Kettle Café Ltd" -> "copper kettle cafe"."""
    return " ".join(name_tokens(name))


def phone_digits(phone: str | None) -> str:
    """The last 9 digits: the same number with or without +44/0/+1 prefixes compares equal."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-9:] if len(digits) >= 9 else ""


def extract_postcode(address: str | None, country: str) -> str | None:
    """The postcode in an address, normalised (UK "m1 1ae" -> "M1 1AE"). AU/US take the last match,
    since street numbers come first."""
    pattern = POSTCODE.get(country)
    if not address or not pattern:
        return None
    matches = list(pattern.finditer(address.upper()))
    if not matches:
        return None
    m = matches[-1]
    return f"{m.group(1)} {m.group(2)}" if country == "UK" else m.group(1)


def normalise_postcode(postcode: str | None, country: str) -> str | None:
    if not postcode:
        return None
    return extract_postcode(postcode, country) or postcode.strip().upper()


def similarity(a: str, b: str) -> float:
    """Share of name tokens in common (0..1), ignoring legal suffixes and filler words."""
    x, y = set(name_tokens(a)), set(name_tokens(b))
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)
