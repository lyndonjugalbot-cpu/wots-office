"""Adapter for our existing scraper (spec v2 §8, §11).

Internal office only, behind the `internal_scraper_enabled` office setting: customer offices source
leads from official APIs only. The scraper's input/output format is still to come (spec §17.1);
until then this adapter says so rather than guessing.
"""
from __future__ import annotations

from ..core.context import OrgContext
from .errors import IntegrationConfigError

FLAG = "internal_scraper_enabled"


def check_allowed(ctx: OrgContext) -> None:
    if not ctx.is_internal:
        raise IntegrationConfigError("Customer offices source leads from official APIs only (Google Places)")
    if not ctx.settings.get(FLAG):
        raise IntegrationConfigError(f"The internal scraper is off for this office (setting {FLAG})")


class ExistingScraper:
    def __init__(self, ctx: OrgContext):
        check_allowed(ctx)

    def search(self, country: str, query: str, region: str | None, limit: int):
        raise IntegrationConfigError("The scraper adapter is waiting for the scraper's input/output format "
                                     "(spec §17 decision 1). Use Google Places meanwhile.")
