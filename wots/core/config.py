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
from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("WOTS_CONFIG_DIR", ROOT / "config"))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AtlasSettings(_Strict):
    tick_seconds: int = 30
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
    model_pricing: dict[str, Pricing] = Field(default_factory=dict)
    outreach: OutreachSettings = OutreachSettings()
    previews: PreviewSettings = PreviewSettings()
    retention_days: int = 180
    notify: NotifySettings = NotifySettings()
    qa: QASettings = QASettings()

    @property
    def data_path(self) -> Path:
        path = Path(self.data_dir)
        return path if path.is_absolute() else ROOT / path

    @property
    def llm_cap_usd(self) -> float:
        """Today's spend limit: the daily budget, lowered further in dry-run mode."""
        cap = self.budget.daily_llm_budget_usd
        return min(cap, self.budget.dry_run_llm_cap_usd) if self.dry_run else cap


class AgentConfig(_Strict):
    name: str = ""
    cls: str = Field(alias="class")
    enabled: bool = False
    scope: list[Literal["website", "ad_refresh"]]
    owns: dict[str, str] = Field(default_factory=dict)
    assigned_only: list[str] = Field(default_factory=list)
    pool: list[str] = Field(default_factory=list)
    style_profile: str | None = None
    uses_llm: bool = False
    model: str | None = None  # "fast", "strong" or an explicit model id
    batch_size: int | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AgentDefaults(_Strict):
    batch_size: int = 5
    fast_model: str = "claude-haiku-4-5"
    strong_model: str = "claude-opus-5"


class AgentsFile(_Strict):
    defaults: AgentDefaults = AgentDefaults()
    agents: dict[str, AgentConfig]

    @field_validator("agents")
    @classmethod
    def _names(cls, agents: dict[str, AgentConfig]) -> dict[str, AgentConfig]:
        for name, agent in agents.items():
            agent.name = name
        return agents

    def model_for(self, agent: AgentConfig) -> str | None:
        if agent.model == "fast":
            return self.defaults.fast_model
        if agent.model == "strong":
            return self.defaults.strong_model
        return agent.model

    def batch_size(self, agent: AgentConfig) -> int:
        return agent.batch_size or self.defaults.batch_size


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


class Config(BaseModel):
    settings: Settings
    agents: AgentsFile
    countries: dict[str, CountryRules]


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
    return Config(settings=Settings(**raw), agents=AgentsFile(**_read(config_dir / "agents.yaml")), countries=countries)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
