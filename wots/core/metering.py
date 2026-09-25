"""Claude calls and usage metering (spec v2 §10).

Every LLM call is costed from config/models.yaml into `usage_events` (USD + credits), per office,
employee and work item. An office is paused when it reaches its daily LLM budget (lower in dry-run
mode) or, for customer offices, when it has no credits left.
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
from .context import OrgContext
from .models import CreditLedger, UsageEvent

FALLBACK_BETA = "server-side-fallback-2026-07-01"
ADAPTIVE_THINKING_MODELS = ("claude-opus-5", "claude-sonnet-5")


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    """This office has spent today's budget (or run out of credits). No work item is at fault."""


class LLMConfigError(LLMError):
    """The API can't be used at all (bad key, missing workspace, no credit balance). No work item is at fault."""


def start_of_local_day(now_utc: datetime, tz: str) -> datetime:
    """Midnight today in the given timezone, as naive UTC."""
    zone = ZoneInfo(tz)
    local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    midnight = datetime.combine(local.date(), time.min, tzinfo=zone)
    return midnight.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


class Meter:
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
            self._client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)
        return self._client

    # ------------------------------------------------------------------ costs and limits

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.config.models.pricing.get(model)
        if price is None:  # an uncosted model would slip past the budget guard
            raise LLMError(f"No price for model '{model}' in config/models.yaml")
        return (input_tokens * price.input + output_tokens * price.output) / 1_000_000

    def daily_cap(self, ctx: OrgContext) -> float:
        settings = self.config.settings
        cap = float(ctx.settings.get("daily_llm_budget_usd", settings.budget.daily_llm_budget_usd))
        return min(cap, settings.budget.dry_run_llm_cap_usd) if settings.dry_run else cap

    def spend_today(self, ctx: OrgContext) -> float:
        since = start_of_local_day(self.clock(), ctx.settings.get("timezone", self.config.settings.timezone))
        with self.sessions() as s:
            return float(s.scalar(select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
                UsageEvent.org_id == ctx.org_id, UsageEvent.kind == "llm", UsageEvent.ts >= since)))

    def credit_balance(self, ctx: OrgContext) -> float | None:
        """None = unlimited (internal offices in Phases 0-6)."""
        if ctx.is_internal and self.config.billing.internal_unlimited_credits:
            return None
        with self.sessions() as s:
            return float(s.scalar(select(func.coalesce(func.sum(CreditLedger.delta), 0.0)).where(
                CreditLedger.org_id == ctx.org_id)))

    def paused_reason(self, ctx: OrgContext) -> str | None:
        spent, cap = self.spend_today(ctx), self.daily_cap(ctx)
        if spent >= cap:
            return (f"today's LLM spend ${spent:.2f} reached the ${cap:.2f} cap"
                    f"{' (dry run)' if self.config.settings.dry_run else ''}")
        balance = self.credit_balance(ctx)
        if balance is not None and balance <= 0:
            return "the office has no credits left"
        return None

    def record(self, ctx: OrgContext, *, kind: str, model: str | None = None, input_tokens: int = 0,
               output_tokens: int = 0, cost_usd: float = 0.0, employee_id: str | None = None,
               work_item_id: str | None = None) -> float:
        credits = self.config.billing.credits(cost_usd)
        with self.sessions.begin() as s:
            s.add(UsageEvent(org_id=ctx.org_id, employee_id=employee_id, work_item_id=work_item_id, kind=kind,
                             model=model, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd,
                             credits=credits, ts=self.clock()))
            if self.credit_balance(ctx) is not None and credits:
                balance = float(s.scalar(select(func.coalesce(func.sum(CreditLedger.delta), 0.0)).where(
                    CreditLedger.org_id == ctx.org_id)))
                s.add(CreditLedger(org_id=ctx.org_id, delta=-credits, reason="usage", ref=work_item_id,
                                   balance_after=balance - credits, ts=self.clock()))
        return credits

    def for_employee(self, ctx: OrgContext, employee_id: str, work_item_id: str | None) -> "EmployeeLLM":
        return EmployeeLLM(self, ctx, employee_id, work_item_id)

    # ------------------------------------------------------------------ calls

    def complete(self, ctx: OrgContext, *, model: str, system: str, messages: list[dict], employee_id: str | None = None,
                 work_item_id: str | None = None, max_tokens: int = 16000, effort: str | None = None, **kwargs):
        reason = self.paused_reason(ctx)
        if reason:
            raise BudgetExceeded(reason)
        if effort:
            if not self.config.models.supports_effort(model):
                raise LLMError(f"{model} doesn't support the effort setting")
            # Lower effort = fewer thinking tokens, which are most of the cost (billed as output)
            kwargs["output_config"] = {**(kwargs.get("output_config") or {}), "effort": effort}
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
            # Account-wide problems: every call would fail the same way
            if any(w in str(e.message).lower() for w in ("workspace", "api key", "credit balance", "billing")):
                raise LLMConfigError(f"Claude API setup problem: {e.message}") from e
            raise LLMError(f"Claude API error 400: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Claude API rate limit reached") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Claude API") from e

        usage = response.usage
        self.record(ctx, kind="llm", model=model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    cost_usd=self.cost(model, usage.input_tokens, usage.output_tokens), employee_id=employee_id,
                    work_item_id=work_item_id)
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined this request")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's reply was cut off (max_tokens reached)")
        return response


class EmployeeLLM:
    """The LLM as one employee sees it: bound to its office and current work item, so usage is attributed."""

    def __init__(self, meter: Meter, ctx: OrgContext, employee_id: str, work_item_id: str | None):
        self.meter, self.ctx, self.employee_id, self.work_item_id = meter, ctx, employee_id, work_item_id

    def complete(self, **kwargs):
        return self.meter.complete(self.ctx, employee_id=self.employee_id, work_item_id=self.work_item_id, **kwargs)
