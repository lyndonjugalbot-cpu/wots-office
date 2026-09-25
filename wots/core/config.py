"""Typed config loaded from config/*.yaml (spec §2.5: config over code).

Env overrides: DRY_RUN, DATABASE_URL, WOTS_DATA_DIR, NOTIFY_WEBHOOK, WOTS_CONFIG_DIR.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("WOTS_CONFIG_DIR", ROOT / "config"))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AtlasSettings(_Strict):
    tick_seconds: int = 30
    max_jobs_per_org_per_tick: int = 10  # so one busy office can't starve the others
    workers: int = 4                     # in-process job workers for `wots run`
    lease_seconds: int = 600
    max_retries: int = 3
    retry_backoff_seconds: int = 30
    max_fix_count: int = 3


class WipSettings(_Strict):
    max_wip: int = 2
    wip_mode: Literal["batch", "rolling"] = "batch"
    fairness_window_days: int = 7


class BudgetSettings(_Strict):
    daily_llm_budget_usd: float = 5.0
    dry_run_llm_cap_usd: float = 1.0


class Pricing(_Strict):
    input: float
    output: float


class OutreachSettings(_Strict):
    daily_send_cap: int = 20
    followup_after_days: int = 5


class PreviewSettings(_Strict):
    preview_ttl_days: int = 30


class ResearchSettings(_Strict):
    """Lead sourcing and verification (spec v2 §8, Phase 2)."""
    # Off: Google's terms don't allow saving business names/addresses from the Places API (Maps Platform
    # ToS 3.2.3), which a lead list needs. Turn on only after checking with Google.
    places_enabled: bool = False
    places_cost_per_request_usd: float = 0.035  # Text Search with phone + website fields; check Google's current price
    max_places_requests_per_day: int = 100      # per office, so a typo in a search can't run up a bill
    page_size: int = 20                         # the most Places returns per page (3 pages per query)
    request_timeout_seconds: float = 10
    website_check_timeout_seconds: float = 6
    # OpenStreetMap (free): Nominatim finds the town, Overpass finds the businesses in it
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    overpass_urls: list[str] = ["https://overpass-api.de/api/interpreter",
                                "https://overpass.private.coffee/api/interpreter"]
    max_osm_requests_per_day: int = 200        # per office; the public servers ask for light use
    companies_house_page_size: int = 100       # advanced company search, UK


class NotifySettings(_Strict):
    webhook: str | None = None
    on_statuses: list[str] = Field(default_factory=lambda: ["READY_FOR_APPROVAL", "PITCH_DRAFTED", "ESCALATED"])


class QASettings(_Strict):
    lighthouse: dict[str, int] = Field(default_factory=dict)
    run_lighthouse: bool = True
    check_external_links: bool = True


class Settings(_Strict):
    dry_run: bool = True
    auto_send: bool = False
    database_url: str = "sqlite:///data/wots.db"
    data_dir: str = "data"
    timezone: str = "Pacific/Auckland"
    atlas: AtlasSettings = AtlasSettings()
    wip: WipSettings = WipSettings()
    budget: BudgetSettings = BudgetSettings()
    outreach: OutreachSettings = OutreachSettings()
    previews: PreviewSettings = PreviewSettings()
    retention_days: int = 180
    notify: NotifySettings = NotifySettings()
    qa: QASettings = QASettings()
    research: ResearchSettings = ResearchSettings()

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        return path if path.is_absolute() else ROOT / path

    @property
    def llm_cap_usd(self) -> float:
        """Today's spend limit: the daily budget, lowered further in dry-run mode."""
        cap = self.budget.daily_llm_budget_usd
        return min(cap, self.budget.dry_run_llm_cap_usd) if self.dry_run else cap


class ModelsConfig(_Strict):
    """config/models.yaml: aliases (fast/strong) and prices."""
    aliases: dict[str, str]
    pricing: dict[str, Pricing]
    no_effort: list[str] = Field(default_factory=list)

    def resolve(self, name: str | None) -> str | None:
        return self.aliases.get(name, name) if name else None

    def supports_effort(self, model: str) -> bool:
        return not any(model.startswith(m) for m in self.no_effort)


class BillingConfig(_Strict):
    credit_value_usd: float = 0.01
    internal_unlimited_credits: bool = True

    def credits(self, cost_usd: float) -> float:
        return round(cost_usd / self.credit_value_usd, 4)


class SendWindow(_Strict):
    days: list[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]]
    start: str
    end: str


class CountryRules(_Strict):
    code: Literal["US", "UK", "AU"]
    spelling: str
    currency: str
    phone_format: str
    channels_allowed: list[str]
    contactable_entity_types: list[str]
    require_postal_address: bool = False
    require_unsubscribe: bool = True
    unsubscribe_honor_days: int | None = None
    require_source_disclosure: bool = False
    require_conspicuous_publication: bool = False
    send_window_local: SendWindow


class Trade(_Strict):
    """config/trades.yaml: what to search for, per source."""
    label: str
    osm: list[tuple[str, str]] = Field(default_factory=list)  # OpenStreetMap key/value tags
    sic: list[str] = Field(default_factory=list)  # UK SIC codes for Companies House


class Config(BaseModel):
    settings: Settings
    models: ModelsConfig
    billing: BillingConfig
    countries: dict[str, CountryRules]
    trades: dict[str, Trade] = Field(default_factory=dict)


def _read(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_config(config_dir: Path = CONFIG_DIR) -> Config:
    # Secrets live in .env (git-ignored); variables already set in the shell win
    load_dotenv(ROOT / ".env", override=False)
    raw = _read(config_dir / "settings.yaml")
    # Environment overrides for things that differ per machine or are secret
    if "DRY_RUN" in os.environ:
        raw["dry_run"] = os.environ["DRY_RUN"].strip().lower() not in {"0", "false", "no", "off"}
    if os.environ.get("DATABASE_URL"):
        raw["database_url"] = os.environ["DATABASE_URL"]
    if os.environ.get("WOTS_DATA_DIR"):
        raw["data_dir"] = os.environ["WOTS_DATA_DIR"]
    if os.environ.get("NOTIFY_WEBHOOK"):
        raw.setdefault("notify", {})["webhook"] = os.environ["NOTIFY_WEBHOOK"]
    countries = {
        (rules := CountryRules(**_read(p))).code: rules for p in sorted((config_dir / "countries").glob("*.yaml"))
    }
    return Config(settings=Settings(**raw), models=ModelsConfig(**_read(config_dir / "models.yaml")),
                  billing=BillingConfig(**_read(config_dir / "billing.yaml")), countries=countries,
                  trades={k: Trade(**v) for k, v in _read(config_dir / "trades.yaml").items()})


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
