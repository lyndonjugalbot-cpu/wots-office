"""OrgContext: which office a piece of code is acting for, and as whom (spec v2 §2.3, §3)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrgContext:
    org_id: str
    slug: str
    name: str
    is_internal: bool = False
    user_id: str | None = None  # None = the platform itself (Atlas, CLI)
    roles: frozenset[str] = field(default_factory=frozenset)
    approvals_delegated: bool = False
    settings: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def is_system(self) -> bool:
        return self.user_id is None

    def can_pass_gate(self, gate_role: str) -> bool:
        """Human gates: the CEO, or a manager the CEO delegated approvals to (spec v2 §3)."""
        if self.is_system:
            return False
        if gate_role in self.roles:
            return True
        return gate_role == "ceo" and "manager" in self.roles and self.approvals_delegated

    @property
    def can_manage_team(self) -> bool:
        return not self.is_system and bool({"owner", "ceo"} & self.roles)

    @property
    def can_edit(self) -> bool:
        return not self.is_system and bool({"owner", "ceo", "manager"} & self.roles)
