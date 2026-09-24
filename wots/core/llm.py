"""Claude calls for the agents, with every call costed into llm_usage (spec §3, §6.6).

Agents call LLM.complete(); it refuses to call once today's spend has reached the cap
(the daily budget, or the lower dry-run cap).
"""
from __future__ import annotations

import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .clock import Clock, utcnow
from .config import Config
from .models import LLMUsage

FALLBACK_BETA = "server-side-fallback-2026-07-01"
ADAPTIVE_THINKING_MODELS = ("claude-opus-5", "claude-sonnet-5")


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


class LLMConfigError(LLMError):
    """The API can't be used at all (bad key, missing workspace): no lead is at fault."""


def start_of_local_day(now_utc: datetime, tz: str) -> datetime:
    """Midnight today in the CEO's timezone, as naive UTC."""
    zone = ZoneInfo(tz)
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    midnight = datetime.combine(local.date(), time.min, tzinfo=zone)
    return midnight.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


class LLM:
    def __init__(self, sessions: sessionmaker[Session], config: Config, clock: Clock = utcnow, client=None):
        self.sessions = sessions
        self.config = config
        self.clock = clock
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            # Keys that aren't scoped to a workspace must name one (ANTHROPIC_WORKSPACE_ID in .env)
            workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
            headers = {"anthropic-workspace-id": workspace} if workspace else None
            self._client = anthropic.Anthropic(default_headers=headers)
        return self._client

    # ------------------------------------------------------------------ spend

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.config.settings.model_pricing.get(model)
        if price is None:  # an uncosted model would slip past the budget guard
            raise LLMError(f"No price for model '{model}' in settings.yaml model_pricing")
        return (input_tokens * price.input + output_tokens * price.output) / 1_000_000

    def spend_today(self) -> float:
        since = start_of_local_day(self.clock(), self.config.settings.timezone)
        with self.sessions() as s:
            return float(s.scalar(select(func.coalesce(func.sum(LLMUsage.cost_usd), 0.0)).where(LLMUsage.ts >= since)))

    def over_budget(self) -> bool:
        return self.spend_today() >= self.config.settings.llm_cap_usd

    def record(self, agent: str, model: str, input_tokens: int, output_tokens: int, lead_id: int | None) -> float:
        cost = self.cost(model, input_tokens, output_tokens)
        with self.sessions.begin() as s:
            s.add(LLMUsage(agent=agent, model=model, input_tokens=input_tokens, output_tokens=output_tokens,
                           cost_usd=cost, lead_id=lead_id, ts=self.clock()))
        return cost

    # ------------------------------------------------------------------ calls

    def complete(self, *, agent: str, model: str, system: str, messages: list[dict], lead_id: int | None = None,
                 max_tokens: int = 16000, **kwargs):
        if self.over_budget():
            raise BudgetExceeded(f"Today's LLM spend has reached ${self.config.settings.llm_cap_usd:.2f}")
        params = dict(model=model, max_tokens=max_tokens, system=system, messages=messages, **kwargs)
        try:
            if model.startswith("claude-opus-5"):
                # If a safety classifier declines, the API retries on its recommended fallback model
                response = self.client.beta.messages.create(
                    **params, thinking={"type": "adaptive"}, betas=[FALLBACK_BETA], fallbacks="default")
            elif model.startswith(ADAPTIVE_THINKING_MODELS):
                response = self.client.messages.create(**params, thinking={"type": "adaptive"})
            else:
                response = self.client.messages.create(**params)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LLMConfigError(f"Claude API key problem: {e.message}") from e
        except anthropic.BadRequestError as e:
            if any(w in str(e.message).lower() for w in ("workspace", "api key")):
                raise LLMConfigError(f"Claude API setup problem: {e.message}") from e
            raise LLMError(f"Claude API error 400: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Claude API rate limit reached") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Claude API") from e

        self.record(agent, model, response.usage.input_tokens, response.usage.output_tokens, lead_id)
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined this request")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's reply was cut off (max_tokens reached)")
        return response

