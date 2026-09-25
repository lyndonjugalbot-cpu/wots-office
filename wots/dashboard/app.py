"""Internal dashboard API (spec v2 §12), localhost only. The 3D office in frontend/ is its UI.

Every /api route resolves an OrgContext from the signed-in user's membership: the office comes
from the `X-Org` header (or `?org=`; a slug), defaulting to the user's first office. Gate actions check the
user's role; hiring and firing need the CEO or owner.
"""
from __future__ import annotations

import io
import json
from datetime import timedelta
from types import SimpleNamespace

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..core import auth
from ..core.board import InvalidTransition
from ..core.cli import import_csv
from ..core.config import ROOT
from ..core.context import OrgContext
from ..core.models import Approval, Artifact, Event, Membership, QAReport, User, WorkItem
from ..core.offices import OfficeError
from ..core.repo import NotFound, scoped
from ..core.runtime import Runtime
from ..integrations.errors import IntegrationConfigError, IntegrationError
from ..orchestration.research import ResearchError, daily_limit, requests_today, run_research
from . import actions

KIND_COLORS = {
    "lead_researcher": "#27ae60", "ad_researcher": "#1abc9c", "data_verifier": "#2c7a7b", "copywriter": "#2980b9",
    "creative_strategist": "#34495e", "graphic_designer": "#e84393", "web_developer": "#e67e22",
    "qa_tester": "#16a085", "deployment": "#7f8c8d", "cold_email": "#f39c12", "hr_manager": "#9b59b6",
    "content_creator": "#c0392b",
}
VERBS = {"lead_researcher": "Checking", "data_verifier": "Verifying", "copywriter": "Writing copy for",
         "graphic_designer": "Designing for", "web_developer": "Building", "qa_tester": "Testing",
         "deployment": "Deploying", "cold_email": "Drafting a pitch for", "creative_strategist": "Briefing"}
RECENT = timedelta(seconds=8)  # how long a 'done' bubble stays up
FAILURE_WINDOW = timedelta(minutes=3)


class Notes(BaseModel):
    notes: str = Field(default="", max_length=4000)


class Disqualify(BaseModel):
    reason: str = Field(default="", max_length=500)


class Resolve(BaseModel):
    to_status: str
    notes: str = Field(default="", max_length=4000)


class Research(BaseModel):
    country: str
    trade: str = Field(min_length=1, max_length=100)
    source: str = "osm"
    regions: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=50, ge=1, le=200)


class Hire(BaseModel):
    type: str
    name: str = Field(min_length=1, max_length=40)
    config: dict = Field(default_factory=dict)


def create_app(rt: Runtime) -> FastAPI:
    app = FastAPI(title="Wots Office")
    board = rt.board

    # ------------------------------------------------------------------ auth & office context

    def current_user(request: Request) -> str:
        token = request.cookies.get(auth.COOKIE)
        if not token:
            raise HTTPException(401, "Sign in with the link printed by `wots run` (or `wots login-link`)")
        try:
            return auth.verify(rt, token, "session")["uid"]
        except auth.AuthError as e:
            raise HTTPException(401, str(e))

    def office(user_id: str = Depends(current_user), x_org: str | None = Header(default=None),
               org: str | None = Query(default=None)) -> OrgContext:
        memberships = rt.offices.memberships(user_id)
        if not memberships:
            raise HTTPException(403, "You aren't a member of any office")
        slug = x_org or org or memberships[0][0].slug  # ?org= is for links and iframes, which can't send headers
        try:
            return rt.offices.user_ctx(user_id, slug)
        except (PermissionError, NotFound):
            raise HTTPException(403, "You aren't a member of that office")

    @app.get("/auth/login")
    def login(token: str):
        try:
            uid = auth.verify(rt, token, "login")["uid"]
        except auth.AuthError as e:
            raise HTTPException(401, f"{e}. Run `bin/wots login-link` for a new one.")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(auth.COOKIE, auth.session_cookie(rt, uid), httponly=True, samesite="lax",
                            max_age=auth.SESSION_SECONDS)
        return response

    @app.post("/auth/logout")
    def logout():
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(auth.COOKIE)
        return response

    @app.get("/api/me")
    def me(user_id: str = Depends(current_user)) -> dict:
        with rt.sessions() as s:
            user = s.scalars(select(User).where(User.id == user_id)).one()
        return {"user": {"id": user.id, "email": user.email, "name": user.name},
                "offices": [{"slug": o.slug, "name": o.name, "roles": sorted(r), "is_internal": o.is_internal}
                            for o, r in rt.offices.memberships(user_id)]}

    def item_json(ctx: OrgContext, item: SimpleNamespace, names: dict[str, str]) -> dict:
        data = dict(vars(item))
        data["assigned_to"] = names.get(item.assigned_employee_id or "")
        return data

    def employee_names(ctx: OrgContext) -> dict[str, str]:
        return {e.id: e.name for e in rt.offices.employees(ctx, include_fired=True)}

    def act(ctx: OrgContext, fn, *args):
        try:
            return item_json(ctx, fn(board, ctx, *args), employee_names(ctx))
        except NotFound:
            raise HTTPException(404, "No such work item in this office")
        except actions.Forbidden as e:
            raise HTTPException(403, str(e))
        except (InvalidTransition, actions.ActionError) as e:
            raise HTTPException(409, str(e))

    # ------------------------------------------------------------------ overview, office, team

    @app.get("/api/overview")
    def overview(ctx: OrgContext = Depends(office)) -> dict:
        with rt.sessions() as s:
            rows = s.execute(select(WorkItem.workflow_key, WorkItem.status, func.count()).where(
                WorkItem.org_id == ctx.org_id).group_by(WorkItem.workflow_key, WorkItem.status)).all()
        counts: dict = {}
        for key, status, n in rows:
            counts.setdefault(key, {})[status] = n
        workflows = []
        gates = {"approvals": 0, "escalations": 0, "other": 0}
        for ow in rt.offices.workflows(ctx):
            wf = rt.catalogue.workflows[ow.workflow_key]
            workflows.append({"key": wf.key, "active": ow.active, "states": list(wf.states),
                              "waiting_for": ow.settings.get("allow_missing", []),
                              "missing_reason": ow.settings.get("missing_reason")})
            for state in wf.gates():
                n = counts.get(wf.key, {}).get(state, 0)
                bucket = "approvals" if state == "READY_FOR_APPROVAL" else "escalations" if state == wf.escalate_to else "other"
                gates[bucket] += n
        staff = rt.atlas.staff(ctx)
        designers = []
        with rt.sessions() as s:
            for st in staff:
                if "max_wip" in st.info.config:
                    active_states = sorted({x for wf in rt.atlas.active_workflows(ctx) for x in wf.wip.get(st.info.type.key, [])})
                    active = s.scalar(select(func.count()).select_from(WorkItem).where(
                        WorkItem.org_id == ctx.org_id, WorkItem.assigned_employee_id == st.info.id,
                        WorkItem.status.in_(active_states))) or 0
                    designers.append({"name": st.info.name, "active": active, "max": st.info.config["max_wip"],
                                      "mode": st.info.config.get("wip_mode"), "style": st.info.config.get("style_profile")})
        return {
            "office": {"slug": ctx.slug, "name": ctx.name, "is_internal": ctx.is_internal, "roles": sorted(ctx.roles)},
            "dry_run": rt.config.settings.dry_run, "auto_send": rt.config.settings.auto_send,
            "spend_today": round(rt.meter.spend_today(ctx), 4), "spend_cap": rt.meter.daily_cap(ctx),
            "credits": rt.meter.credit_balance(ctx), "counts": counts, "workflows": workflows,
            "designers": designers, "pending": gates, "tick_seconds": rt.config.settings.atlas.tick_seconds,
            "research": {src: {"requests_today": requests_today(rt, ctx, src), "max_per_day": daily_limit(rt, src)}
                         for src in ("osm", "companies_house")},
        }

    @app.get("/api/office")
    def office_view(ctx: OrgContext = Depends(office)) -> dict:
        now = rt.atlas.clock()
        staff = rt.atlas.staff(ctx)
        with rt.sessions() as s:
            claimed = {i.claimed_by: i.id for i in s.scalars(scoped(WorkItem, ctx).where(
                WorkItem.claimed_by.is_not(None), WorkItem.claim_expires_at > now))}
            recent = list(s.scalars(scoped(Event, ctx).where(Event.ts >= now - FAILURE_WINDOW).order_by(Event.seq.desc())))
            ceo_name = s.scalars(select(User.name).join(Membership, Membership.user_id == User.id).where(
                Membership.org_id == ctx.org_id, Membership.role == "ceo")).first()
        pending = sum(overview(ctx)["pending"].values())
        names = {i.id: getattr(i, "business_name", "item") for i in board.items(ctx, statuses=None, limit=500)}
        people = [
            {"id": "ceo", "name": ceo_name or "CEO", "title": "CEO", "color": "#c0392b", "kind": "ceo",
             "status": "waiting" if pending else "idle",
             "bubble": f"{pending} item{'s' if pending != 1 else ''} need{'' if pending != 1 else 's'} me" if pending else "",
             "style": None, "available": True},
            {"id": "atlas", "name": "Atlas", "title": "Office Manager", "color": "#8e44ad", "kind": "atlas",
             "status": "idle", "bubble": "", "style": None, "available": True},
        ]
        first_qa = next((st.info.id for st in staff if st.info.type.key == "qa_tester"), None)
        for st in staff:
            emp = st.info
            status, bubble = "idle", ""
            mine = [e for e in recent if e.actor_id == emp.id]
            if emp.id in claimed:
                status, bubble = "working", f"{VERBS.get(emp.type.key, 'Working on')} {names.get(claimed[emp.id], 'an item')}"
            elif mine and mine[0].note and mine[0].note.startswith("error:"):
                status, bubble = "error", "Hit a problem; retrying"
            elif mine and now - mine[0].ts <= RECENT:
                e = mine[0]
                status = "done"
                bubble = (f"{names.get(e.work_item_id, 'Item')} → {e.to_status}" if e.to_status and e.from_status != e.to_status
                          else (e.note or "")[:60])
            people.append({"id": emp.id, "name": emp.name, "title": emp.type.display_name,
                           "color": KIND_COLORS.get(emp.type.key, "#4a90d9"),
                           "kind": "qa" if emp.id == first_qa else "worker", "status": status, "bubble": bubble,
                           "style": emp.config.get("style_profile"), "available": st.impl is not None,
                           "type": emp.type.key})
        return {"agents": people}

    @app.get("/api/team")
    def team(ctx: OrgContext = Depends(office)) -> dict:
        staff = {st.info.id: st for st in rt.atlas.staff(ctx)}
        members = []
        for emp in rt.offices.employees(ctx):
            type_def = rt.catalogue.types.get(emp.type_key)
            members.append({"id": emp.id, "name": emp.name, "type": emp.type_key,
                            "type_name": type_def.display_name if type_def else emp.type_key, "config": emp.config,
                            "enabled": emp.enabled, "working": emp.id in staff and staff[emp.id].impl is not None})
        catalogue = [{"key": t.key, "name": t.display_name, "description": t.description, "risk": t.risk_level,
                      "status": t.status, "config_schema": t.config_schema} for t in rt.catalogue.types.values()]
        return {"members": members, "catalogue": catalogue, "can_manage": ctx.can_manage_team}

    @app.post("/api/team/hire")
    def hire(body: Hire, ctx: OrgContext = Depends(office)) -> dict:
        if not ctx.can_manage_team:
            raise HTTPException(403, "Only the CEO or owner can hire")
        try:
            emp = rt.offices.hire(ctx, body.type, body.name.strip(), body.config)
        except OfficeError as e:
            raise HTTPException(400, str(e))
        return {"id": emp.id, "name": emp.name}

    @app.post("/api/team/{employee_id}/fire")
    def fire(employee_id: str, ctx: OrgContext = Depends(office)) -> dict:
        if not ctx.can_manage_team:
            raise HTTPException(403, "Only the CEO or owner can let someone go")
        with rt.sessions() as s:
            busy = s.scalar(select(func.count()).select_from(WorkItem).where(
                WorkItem.org_id == ctx.org_id, WorkItem.assigned_employee_id == employee_id,
                WorkItem.status.notin_(["APPROVED", "DISQUALIFIED", "WON", "LOST"])))
        if busy:
            raise HTTPException(409, "They still have work assigned. Wait until it's approved or reassign it.")
        try:
            rt.offices.fire(ctx, employee_id)
        except NotFound:
            raise HTTPException(404, "No such employee in this office")
        return {"ok": True}

    @app.get("/api/events")
    def events(after: int = 0, limit: int = 100, ctx: OrgContext = Depends(office)) -> list[dict]:
        names = employee_names(ctx)
        with rt.sessions() as s:
            rows = list(s.scalars(scoped(Event, ctx).where(Event.seq > after).order_by(Event.seq.desc()).limit(min(limit, 500))))
        items = {i.id: getattr(i, "business_name", None) for i in board.items(ctx, limit=1000)}
        out = []
        for e in reversed(rows):
            actor = names.get(e.actor_id or "") or ("You" if e.actor_kind == "user" else (e.actor_id or "system"))
            out.append({"id": e.seq, "item_id": e.work_item_id, "business": items.get(e.work_item_id), "from": e.from_status,
                        "to": e.to_status, "actor": actor, "actor_kind": e.actor_kind, "note": e.note,
                        "ts": e.ts.isoformat() + "Z"})
        return out

    @app.post("/api/tick")
    async def tick(ctx: OrgContext = Depends(office)) -> dict:
        report = await run_in_threadpool(rt.atlas.tick, ctx.slug)
        return {"ran": report is not None, "summary": report.summary() if report else "A tick is already running"}

    # ------------------------------------------------------------------ work items

    @app.get("/api/items")
    def items(status: str | None = None, workflow: str | None = None, ctx: OrgContext = Depends(office)) -> list[dict]:
        names = employee_names(ctx)
        found = board.items(ctx, workflow=workflow, statuses=status.split(",") if status else None, limit=500,
                            newest_first=True)
        return [item_json(ctx, i, names) for i in found]

    @app.get("/api/items/{item_id}")
    def item_detail(item_id: str, ctx: OrgContext = Depends(office)) -> dict:
        try:
            item = board.get(ctx, item_id)
        except NotFound:
            raise HTTPException(404, "No such work item in this office")
        wf = board.workflow(item.workflow_key)
        folder = rt.files.item_dir(ctx.org_id, item_id, create=False)
        names = employee_names(ctx)
        with rt.sessions() as s:
            evs = list(s.scalars(scoped(Event, ctx).where(Event.work_item_id == item_id).order_by(Event.seq)))
            artifacts = list(s.scalars(scoped(Artifact, ctx).where(Artifact.work_item_id == item_id).order_by(Artifact.created_at)))
            approvals = list(s.scalars(scoped(Approval, ctx).where(Approval.work_item_id == item_id).order_by(Approval.decided_at)))
            qa_runs = s.scalar(select(func.count()).select_from(QAReport).where(
                QAReport.org_id == ctx.org_id, QAReport.work_item_id == item_id))
        qa_path, copy_path = folder / "qa" / "qa_report.json", folder / "copy.json"
        resume = None
        if item.status == wf.escalate_to:
            prev = next((e.from_status for e in reversed(evs) if e.to_status == item.status and e.from_status != item.status), None)
            if prev and wf.states[prev].employee_owned and prev != (wf.fix_loop or {}).get("counter_on"):
                resume = prev
        state = wf.states[item.status]
        return {
            "item": item_json(ctx, item, names),
            "gate": state.value if state.owner == "gate" else None,
            "can_decide": state.owner == "gate" and ctx.can_pass_gate(state.value),
            # From an escalation, the workflow's way back to the assigned employee (BUILDING, DESIGNING...)
            "send_back_to": next((t for t in wf.outgoing(item.status) if wf.states[t].owner == "assigned"), None)
            if item.status == wf.escalate_to and item.assigned_employee_id else None,
            "events": [{"id": e.seq, "from": e.from_status, "to": e.to_status,
                        "actor": names.get(e.actor_id or "") or ("You" if e.actor_kind == "user" else e.actor_id),
                        "note": e.note, "ts": e.ts.isoformat() + "Z"} for e in evs],
            "artifacts": [{"kind": a.kind, "path": a.path, "version": a.version,
                           "by": names.get(a.created_by_employee_id or "", "")} for a in artifacts],
            "approvals": [{"kind": a.kind, "decision": a.decision, "notes": a.notes, "at": a.decided_at.isoformat() + "Z"}
                          for a in approvals],
            "qa": json.loads(qa_path.read_text()) if qa_path.exists() else None, "qa_runs": qa_runs,
            "copy": json.loads(copy_path.read_text()) if copy_path.exists() else None,
            "has_site": (folder / "site" / "index.html").exists(), "resume_status": resume,
            "files_base": f"/api/files/{auth.file_token(rt, ctx.org_id, item_id)}/",
        }

    def serve_file(ctx: OrgContext, item_id: str, path: str) -> FileResponse:
        try:
            board.get(ctx, item_id)  # the item must belong to this office
            target = rt.files.resolve(ctx.org_id, item_id, path)
        except (NotFound, PermissionError):
            raise HTTPException(404, "File not found")
        if not target.is_file():
            raise HTTPException(404, "File not found")
        # Built sites are previewed sandboxed: no access to this dashboard or its API
        headers = {"Content-Security-Policy": "sandbox allow-scripts"} if target.suffix in {".html", ".htm", ".svg"} else {}
        return FileResponse(target, headers=headers)

    @app.get("/api/items/{item_id}/files/{path:path}")
    def item_file(item_id: str, path: str, ctx: OrgContext = Depends(office)) -> FileResponse:
        return serve_file(ctx, item_id, path)

    @app.get("/api/files/{token}/{path:path}")
    def item_file_signed(token: str, path: str) -> FileResponse:
        """Files via a signed, expiring link to one item's folder. Sandboxed site previews have an opaque
        origin, so their requests for styles.css or logo.svg carry no session cookie."""
        try:
            grant = auth.verify(rt, token, "files")
            ctx = rt.offices.system_ctx(grant["org"])
        except (auth.AuthError, NotFound):
            raise HTTPException(404, "File not found")
        return serve_file(ctx, grant["item"], path)

    @app.post("/api/items/{item_id}/approve")
    def approve(item_id: str, body: Notes, ctx: OrgContext = Depends(office)) -> dict:
        return act(ctx, actions.approve_build, item_id, body.notes or None)

    @app.post("/api/items/{item_id}/reject")
    def reject(item_id: str, body: Notes, ctx: OrgContext = Depends(office)) -> dict:
        return act(ctx, actions.reject_build, item_id, body.notes)

    @app.post("/api/items/{item_id}/disqualify")
    def disqualify(item_id: str, body: Disqualify, ctx: OrgContext = Depends(office)) -> dict:
        return act(ctx, actions.disqualify, item_id, body.reason)

    @app.post("/api/items/{item_id}/resolve")
    def resolve(item_id: str, body: Resolve, ctx: OrgContext = Depends(office)) -> dict:
        return act(ctx, actions.resolve_escalation, item_id, body.to_status, body.notes or None)

    # ------------------------------------------------------------------ intake

    def _import(ctx: OrgContext, lines, filename: str, workflow: str) -> dict:
        if not ctx.can_edit:
            raise HTTPException(403, "Viewers can't import leads")
        try:
            created, skipped = import_csv(rt, ctx, lines, filename, workflow)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"created": created, "skipped": skipped}

    @app.post("/api/import")
    async def import_leads(file: UploadFile = File(...), workflow: str = Form("website"),
                           ctx: OrgContext = Depends(office)) -> dict:
        raw = (await file.read()).decode("utf-8-sig", errors="replace")
        return _import(ctx, io.StringIO(raw), file.filename or "upload.csv", workflow)

    @app.get("/api/trades")
    def trades(ctx: OrgContext = Depends(office)) -> dict:
        return {"trades": [{"key": k, "label": t.label, "uk_registry": bool(t.sic)} for k, t in sorted(
                    rt.config.trades.items(), key=lambda kv: kv[1].label)],
                "sources": [{"key": "osm", "label": "OpenStreetMap", "countries": ["US", "UK", "AU"]},
                            {"key": "companies_house", "label": "Companies House", "countries": ["UK"]}]}

    @app.post("/api/research")
    async def research(body: Research, ctx: OrgContext = Depends(office)) -> dict:
        """Find leads in a free source (OpenStreetMap, or Companies House for the UK)."""
        if not ctx.can_edit:
            raise HTTPException(403, "Viewers can't start research")
        try:
            report = await run_in_threadpool(run_research, rt, ctx, country=body.country, trade=body.trade,
                                             regions=[r for r in body.regions if r.strip()], limit=body.limit,
                                             source=body.source)
        except ResearchError as e:
            raise HTTPException(400, str(e))
        except IntegrationError as e:
            raise HTTPException(409 if isinstance(e, IntegrationConfigError) else 502, str(e))
        return {"created": len(report.created), "requests": report.requests, "summary": report.summary()}

    @app.post("/api/import-samples")
    def import_samples(ctx: OrgContext = Depends(office)) -> dict:
        path = ROOT / "samples" / "phase1_leads.csv"
        with path.open(newline="", encoding="utf-8-sig") as f:
            return _import(ctx, f, path.name, "website")

    # ------------------------------------------------------------------ the 3D office UI

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="office")
    return app

