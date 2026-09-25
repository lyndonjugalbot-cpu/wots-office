"""Lead Researcher (Scout in the internal office), spec v2 §8.

Finding leads is a research run (`wots research`, or Find leads in the dashboard; see
wots/orchestration/research.py), which puts them on the board at NEW. As the owner of NEW, the
researcher then hands each one, and each CSV import, to verification.
"""
from __future__ import annotations

from ..base import BaseEmployee, EmployeeContext, EmployeeResult


class LeadResearcher(BaseEmployee):
    type_key = "lead_researcher"

    def run(self, lead, task: str, ctx: EmployeeContext) -> EmployeeResult:
        source = lead.source or "an unknown source"
        return EmployeeResult("VERIFY", f"Lead from {source}; handed to verification")
