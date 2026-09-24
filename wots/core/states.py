"""Lead statuses and the allowed transitions for each scope (spec §5).

board.transition() rejects anything not listed here. Additions beyond the two diagrams,
each needed by another part of the spec:

* READY_FOR_APPROVAL -> DISQUALIFIED: the approval queue has a Disqualify button (§10).
* ad_refresh ESCALATED -> DESIGNING / DISQUALIFIED: the ad diagram lets leads reach
  ESCALATED but gives no way out; these mirror the website scope.
* ad_refresh PITCHED -> LOST: Echo's single follow-up rule applies to both scopes (§7).
* <any working status> -> ESCALATED: Atlas escalates after an agent fails every retry (§6.4).
* ESCALATED -> <the status it was escalated from>: the CEO resuming an error escalation.
  This one depends on the lead's history, so board.transition() checks it separately.
"""
from __future__ import annotations

from enum import StrEnum


class Scope(StrEnum):
    WEBSITE = "website"
    AD_REFRESH = "ad_refresh"


class Status(StrEnum):
    NEW = "NEW"
    ENRICHED = "ENRICHED"
    DISQUALIFIED = "DISQUALIFIED"
    COPY_READY = "COPY_READY"
    ASSETS_READY = "ASSETS_READY"
    BRIEFED = "BRIEFED"
    ASSIGNED = "ASSIGNED"
    BUILDING = "BUILDING"
    DESIGNING = "DESIGNING"
    IN_QA = "IN_QA"
    NEEDS_FIX = "NEEDS_FIX"
    ESCALATED = "ESCALATED"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    APPROVED = "APPROVED"
    PREVIEW_DEPLOYED = "PREVIEW_DEPLOYED"
    PITCH_DRAFTED = "PITCH_DRAFTED"
    PITCH_APPROVED = "PITCH_APPROVED"
    PITCHED = "PITCHED"
    REPLIED = "REPLIED"
    WON = "WON"
    LOST = "LOST"


S = Status

WEBSITE_TRANSITIONS: dict[tuple[Status, Status], str] = {
    (S.NEW, S.ENRICHED): "Ledger: verified no website",
    (S.NEW, S.DISQUALIFIED): "Ledger: has site / closed / dupe / not contactable / fails country rule",
    (S.ENRICHED, S.COPY_READY): "Quill",
    (S.COPY_READY, S.ASSETS_READY): "Iris: logo + hero",
    (S.ASSETS_READY, S.ASSIGNED): "Atlas: Pixel or Nova, respecting WIP",
    (S.ASSIGNED, S.BUILDING): "designer starts",
    (S.BUILDING, S.IN_QA): "designer done",
    (S.IN_QA, S.READY_FOR_APPROVAL): "Hawk pass",
    (S.IN_QA, S.NEEDS_FIX): "Hawk fail",
    (S.NEEDS_FIX, S.BUILDING): "Atlas: back to the same designer",
    (S.NEEDS_FIX, S.ESCALATED): "Atlas: fix_count >= max",
    (S.READY_FOR_APPROVAL, S.APPROVED): "CEO",
    (S.READY_FOR_APPROVAL, S.NEEDS_FIX): "CEO rejects with notes",
    (S.READY_FOR_APPROVAL, S.DISQUALIFIED): "CEO disqualifies",
    (S.APPROVED, S.PREVIEW_DEPLOYED): "Dock",
    (S.PREVIEW_DEPLOYED, S.PITCH_DRAFTED): "Echo",
    (S.PITCH_DRAFTED, S.PITCH_APPROVED): "CEO",
    (S.PITCH_APPROVED, S.PITCHED): "Echo: scheduled send",
    (S.PITCHED, S.REPLIED): "CEO marks reply",
    (S.REPLIED, S.WON): "CEO",
    (S.REPLIED, S.LOST): "CEO",
    (S.PITCHED, S.LOST): "Echo: no reply after follow-up window",
    (S.ESCALATED, S.BUILDING): "CEO sends back",
    (S.ESCALATED, S.DISQUALIFIED): "CEO drops",
}

AD_REFRESH_TRANSITIONS: dict[tuple[Status, Status], str] = {
    (S.NEW, S.ENRICHED): "Ledger: contact found",
    (S.NEW, S.DISQUALIFIED): "Ledger: big brand / no contact / fails country rule",
    (S.ENRICHED, S.BRIEFED): "Lens",
    (S.BRIEFED, S.ASSIGNED): "Atlas: Iris or Juno, respecting WIP",
    (S.ASSIGNED, S.DESIGNING): "designer starts",
    (S.DESIGNING, S.IN_QA): "designer done",
    (S.IN_QA, S.READY_FOR_APPROVAL): "Hawk pass",
    (S.IN_QA, S.NEEDS_FIX): "Hawk fail",
    (S.NEEDS_FIX, S.DESIGNING): "Atlas: back to the same designer",
    (S.NEEDS_FIX, S.ESCALATED): "Atlas: fix_count >= max",
    (S.READY_FOR_APPROVAL, S.APPROVED): "CEO",
    (S.READY_FOR_APPROVAL, S.NEEDS_FIX): "CEO rejects with notes",
    (S.READY_FOR_APPROVAL, S.DISQUALIFIED): "CEO disqualifies",
    (S.APPROVED, S.PITCH_DRAFTED): "Echo",
    (S.PITCH_DRAFTED, S.PITCH_APPROVED): "CEO",
    (S.PITCH_APPROVED, S.PITCHED): "Echo",
    (S.PITCHED, S.REPLIED): "CEO marks reply",
    (S.REPLIED, S.WON): "CEO",
    (S.REPLIED, S.LOST): "CEO",
    (S.PITCHED, S.LOST): "Echo: no reply after follow-up window",
    (S.ESCALATED, S.DESIGNING): "CEO sends back",
    (S.ESCALATED, S.DISQUALIFIED): "CEO drops",
}

# Statuses where an agent (or Atlas) does work, so an exhausted retry can escalate from them
WORKING_STATUSES: dict[Scope, set[Status]] = {
    Scope.WEBSITE: {S.NEW, S.ENRICHED, S.COPY_READY, S.ASSETS_READY, S.ASSIGNED, S.BUILDING,
                    S.IN_QA, S.APPROVED, S.PREVIEW_DEPLOYED, S.PITCH_APPROVED},
    Scope.AD_REFRESH: {S.NEW, S.ENRICHED, S.BRIEFED, S.ASSIGNED, S.DESIGNING, S.IN_QA,
                       S.APPROVED, S.PITCH_APPROVED},
}

TRANSITIONS: dict[Scope, dict[tuple[Status, Status], str]] = {
    Scope.WEBSITE: WEBSITE_TRANSITIONS,
    Scope.AD_REFRESH: AD_REFRESH_TRANSITIONS,
}
for _scope, _table in TRANSITIONS.items():
    for _status in WORKING_STATUSES[_scope]:
        _table.setdefault((_status, S.ESCALATED), "Atlas: retries exhausted")

# The status each scope's designers work in, and the status Atlas assigns from (spec §6.2)
WORK_STATUS: dict[Scope, Status] = {Scope.WEBSITE: S.BUILDING, Scope.AD_REFRESH: S.DESIGNING}
READY_TO_ASSIGN: dict[Scope, Status] = {Scope.WEBSITE: S.ASSETS_READY, Scope.AD_REFRESH: S.BRIEFED}

# Leads in these statuses count toward a designer's WIP (spec §6.2)
ACTIVE_STATUSES = frozenset({S.ASSIGNED, S.BUILDING, S.DESIGNING, S.IN_QA, S.NEEDS_FIX,
                             S.READY_FOR_APPROVAL, S.ESCALATED})
TERMINAL_STATUSES = frozenset({S.DISQUALIFIED, S.WON, S.LOST})


def statuses_for(scope: Scope) -> set[Status]:
    table = TRANSITIONS[scope]
    return {s for pair in table for s in pair}


def is_allowed(scope: Scope, from_status: Status, to_status: Status) -> bool:
    return (Status(from_status), Status(to_status)) in TRANSITIONS[Scope(scope)]
