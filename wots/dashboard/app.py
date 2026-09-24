"""CEO dashboard API (spec §10), localhost only. The 3D office in frontend/ is its UI.

Pages it serves data for: pipeline, approval queue, escalations, lead detail, intake, and a
live "office" view of which agent is doing what, derived from claims and recent events.
"""
from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..core.board import Board, InvalidTransition, LeadNotFound
from ..core.cli import import_csv
from ..core.config import ROOT
from ..core.models import Approval, Artifact, Event, Lead, QAReport
from ..core.runtime import Runtime
from ..core.states import ACTIVE_STATUSES, WORKING_STATUSES, Scope, Status
from . import actions

# How each agent appears in the office. Order = desk order.
DESKS = {
    "ceo": {"name": "You", "title": "CEO", "color": "#c0392b", "kind": "ceo"},
    "atlas": {"name": "Atlas", "title": "Orchestrator", "color": "#8e44ad", "kind": "atlas"},
    "hawk": {"name": "Hawk", "title": "QA", "color": "#16a085", "kind": "worker"},
    "ledger": {"name": "Ledger", "title": "Lead research", "color": "#2c7a7b", "kind": "worker"},
    "quill": {"name": "Quill", "title": "Copywriter", "color": "#2980b9", "kind": "worker"},
    "iris": {"name": "Iris", "title": "Graphic designer", "color": "#e84393", "kind": "worker"},
    "pixel": {"name": "Pixel", "title": "Web designer", "color": "#e67e22", "kind": "worker"},
    "nova": {"name": "Nova", "title": "Web designer", "color": "#d35400", "kind": "worker"},
    "juno": {"name": "Juno", "title": "Graphic designer", "color": "#9b59b6", "kind": "worker"},
    "lens": {"name": "Lens", "title": "Ad analyst", "color": "#34495e", "kind": "worker"},
    "scout": {"name": "Scout", "title": "Lead finder", "color": "#27ae60", "kind": "worker"},
    "scout_ads": {"name": "Scout-Ads", "title": "Ad intake", "color": "#1abc9c", "kind": "worker"},
    "dock": {"name": "Dock", "title": "Deploys", "color": "#7f8c8d", "kind": "worker"},
    "echo": {"name": "Echo", "title": "Outreach", "color": "#f39c12", "kind": "worker"},
}
VERBS = {"ledger": "Enriching", "quill": "Writing copy for", "iris": "Designing a logo for", "pixel": "Building",
         "nova": "Building", "hawk": "Testing", "dock": "Deploying", "echo": "Drafting a pitch for"}
RECENT = timedelta(seconds=8)  # how long a 'done' bubble stays up
FAILURE_WINDOW = timedelta(minutes=3)


class Notes(BaseModel):
    notes: str = Field(default="", max_length=4000)


class Disqualify(BaseModel):
    reason: str = Field(default="", max_length=500)


class Resolve(BaseModel):
    to_status: str
    notes: str = Field(default="", max_length=4000)


def _lead_dict(lead: Lead) -> dict:
    return {c.name: getattr(lead, c.name) for c in Lead.__table__.columns}


def create_app(rt: Runtime) -> FastAPI:
    app = FastAPI(title="Wots Office")
    board: Board = rt.board
    settings = rt.config.settings
    leads_root = settings.data_path / "leads"

    def lead_or_404(lead_id: int) -> Lead:
        try:
            return board.get(lead_id)
        except LeadNotFound:
            raise HTTPException(404, "No such lead")

    def act(fn, *args):
        try:
            return _lead_dict(fn(board, *args))
        except LeadNotFound:
            raise HTTPException(404, "No such lead")
        except (InvalidTransition, actions.ActionError) as e:
            raise HTTPException(409, str(e))

    # ------------------------------------------------------------------ overview & office

    @app.get("/api/overview")
    def overview() -> dict:
        with rt.sessions() as s:
            rows = s.execute(select(Lead.scope, Lead.status, func.count()).group_by(Lead.scope, Lead.status)).all()
            designers = []
            for cfg in sorted(rt.config.agents.agents.values(), key=lambda c: c.name):
                if cfg.pool and cfg.enabled:
                    active = s.scalar(select(func.count()).select_from(Lead).where(
                        Lead.assigned_to == cfg.name, Lead.status.in_([st.value for st in ACTIVE_STATUSES])))
                    designers.append({"name": cfg.name, "active": active, "max": settings.wip.max_wip,
                                      "style": cfg.style_profile})
        counts: dict = {scope.value: {} for scope in Scope}
        for scope, status, n in rows:
            counts[scope][status] = n
        pending = {
            "approvals": sum(c.get("READY_FOR_APPROVAL", 0) for c in counts.values()),
            "escalations": sum(c.get("ESCALATED", 0) for c in counts.values()),
        }
        return {
            "dry_run": settings.dry_run, "auto_send": settings.auto_send, "wip_mode": settings.wip.wip_mode,
            "spend_today": round(rt.llm.spend_today(), 4), "spend_cap": settings.llm_cap_usd,
            "counts": counts, "designers": designers, "pending": pending,
            "tick_seconds": settings.atlas.tick_seconds,
        }

    @app.get("/api/office")
    def office() -> dict:
        now = rt.atlas.clock()
        enabled = {name for name in rt.atlas.agents}
        with rt.sessions() as s:
            claimed = {l.claimed_by: l for l in s.scalars(select(Lead).where(
                Lead.claimed_by.is_not(None), Lead.claim_expires_at > now))}
            recent = list(s.scalars(select(Event).where(Event.ts >= now - FAILURE_WINDOW).order_by(Event.id.desc())))
            names = dict(s.execute(select(Lead.id, Lead.business_name)).all())
            pending = s.scalar(select(func.count()).select_from(Lead).where(
                Lead.status.in_([Status.READY_FOR_APPROVAL.value, Status.ESCALATED.value]))) or 0
        people = []
        for agent_id, desk in DESKS.items():
            if agent_id not in enabled and agent_id not in {"ceo", "atlas"}:
                continue
            status, bubble = "idle", ""
            mine = [e for e in recent if e.actor == agent_id]
            if agent_id == "ceo":
                if pending:
                    status, bubble = "waiting", f"{pending} item{'s' if pending != 1 else ''} need{'' if pending != 1 else 's'} me"
            elif agent_id in claimed and not claimed[agent_id].claimed_by.endswith(":retry"):
                lead = claimed[agent_id]
                status, bubble = "working", f"{VERBS.get(agent_id, 'Working on')} {lead.business_name}"
            elif mine and mine[0].note and mine[0].note.startswith("error:"):
                status, bubble = "error", "Hit a problem; retrying"
            elif mine and now - mine[0].ts <= RECENT:
                e = mine[0]
                status = "done"
                bubble = f"{names.get(e.lead_id, 'Lead')} → {e.to_status}" if e.to_status and e.from_status != e.to_status else (e.note or "")[:60]
            people.append({"id": agent_id, **desk, "status": status, "bubble": bubble,
                           "style": rt.config.agents.agents[agent_id].style_profile if agent_id in rt.config.agents.agents else None})
        return {"agents": people}

    @app.get("/api/events")
    def events(after: int = 0, limit: int = 100) -> list[dict]:
        with rt.sessions() as s:
            rows = s.execute(select(Event, Lead.business_name).outerjoin(Lead, Lead.id == Event.lead_id)
                             .where(Event.id > after).order_by(Event.id.desc()).limit(min(limit, 500))).all()
        return [{"id": e.id, "lead_id": e.lead_id, "business": name, "from": e.from_status, "to": e.to_status,
                 "actor": e.actor, "note": e.note, "ts": e.ts.isoformat() + "Z"} for e, name in reversed(rows)]

    @app.post("/api/tick")
    async def tick() -> dict:
        report = await run_in_threadpool(rt.atlas.tick)
        return {"ran": report is not None, "summary": report.summary() if report else "A tick is already running"}

    # ------------------------------------------------------------------ leads

    @app.get("/api/leads")
    def leads(status: str | None = None, scope: str | None = None) -> list[dict]:
        query = select(Lead).order_by(Lead.updated_at.desc())
        if status:
            query = query.where(Lead.status.in_(status.split(",")))
        if scope:
            query = query.where(Lead.scope == scope)
        with rt.sessions() as s:
            return [_lead_dict(l) for l in s.scalars(query.limit(500))]

    @app.get("/api/leads/{lead_id}")
    def lead_detail(lead_id: int) -> dict:
        lead = lead_or_404(lead_id)
        folder = leads_root / str(lead_id)
        with rt.sessions() as s:
            events_ = list(s.scalars(select(Event).where(Event.lead_id == lead_id).order_by(Event.id)))
            artifacts = list(s.scalars(select(Artifact).where(Artifact.lead_id == lead_id).order_by(Artifact.id)))
            approvals = list(s.scalars(select(Approval).where(Approval.lead_id == lead_id).order_by(Approval.id)))
            qa_count = s.scalar(select(func.count()).select_from(QAReport).where(QAReport.lead_id == lead_id))
        qa = json.loads((folder / "qa" / "qa_report.json").read_text()) if (folder / "qa" / "qa_report.json").exists() else None
        copy = json.loads((folder / "copy.json").read_text()) if (folder / "copy.json").exists() else None
        resume = None
        if lead.status == Status.ESCALATED.value:
            prev = next((e.from_status for e in reversed(events_) if e.to_status == "ESCALATED" and e.from_status != "ESCALATED"), None)
            if prev in {s.value for s in WORKING_STATUSES[Scope(lead.scope)]}:
                resume = prev
        return {
            "lead": _lead_dict(lead),
            "events": [{"id": e.id, "from": e.from_status, "to": e.to_status, "actor": e.actor, "note": e.note,
                        "ts": e.ts.isoformat() + "Z"} for e in events_],
            "artifacts": [{"kind": a.kind, "path": a.path, "version": a.version, "by": a.created_by} for a in artifacts],
            "approvals": [{"kind": a.kind, "decision": a.decision, "notes": a.notes, "at": a.decided_at.isoformat() + "Z"} for a in approvals],
            "qa": qa, "qa_runs": qa_count, "copy": copy,
            "has_site": (folder / "site" / "index.html").exists(),
            "resume_status": resume,
        }

    @app.get("/api/leads/{lead_id}/files/{path:path}")
    def lead_file(lead_id: int, path: str) -> FileResponse:
        root = (leads_root / str(lead_id)).resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "File not found")
        # Built sites are previewed sandboxed: no access to this dashboard or its API
        headers = {"Content-Security-Policy": "sandbox allow-scripts"} if target.suffix in {".html", ".htm", ".svg"} else {}
        return FileResponse(target, headers=headers)

    @app.post("/api/leads/{lead_id}/approve")
    def approve(lead_id: int, body: Notes) -> dict:
        return act(actions.approve_build, lead_id, body.notes or None)

    @app.post("/api/leads/{lead_id}/reject")
    def reject(lead_id: int, body: Notes) -> dict:
        return act(actions.reject_build, lead_id, body.notes)

    @app.post("/api/leads/{lead_id}/disqualify")
    def disqualify(lead_id: int, body: Disqualify) -> dict:
        return act(actions.disqualify, lead_id, body.reason)

    @app.post("/api/leads/{lead_id}/resolve")
    def resolve(lead_id: int, body: Resolve) -> dict:
        lead = lead_or_404(lead_id)
        if lead.status != Status.ESCALATED.value:
            raise HTTPException(409, "This lead isn't escalated")
        return act(actions.resolve_escalation, lead_id, body.to_status, body.notes or None)

    # ------------------------------------------------------------------ intake

    @app.post("/api/import")
    async def import_leads(file: UploadFile = File(...), scope: str = Form("website")) -> dict:
        if scope not in {s.value for s in Scope}:
            raise HTTPException(400, "Unknown scope")
        raw = (await file.read()).decode("utf-8-sig", errors="replace")
        try:
            created, skipped = import_csv(board, io.StringIO(raw), file.filename or "upload.csv", scope)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"created": created, "skipped": skipped}

    @app.post("/api/import-samples")
    def import_samples() -> dict:
        path = ROOT / "samples" / "phase1_leads.csv"
        with path.open(newline="", encoding="utf-8-sig") as f:
            created, skipped = import_csv(board, f, path.name, "website")
        return {"created": created, "skipped": skipped}

    # ------------------------------------------------------------------ the 3D office UI

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="office")
    return app
