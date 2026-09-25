"""Multi-tenant isolation (spec v2 §2.3, Phase 0 acceptance): a second office can't read or change
the first office's items, files, employees or suppression list, and a query that forgets org_id fails."""
import pytest
from sqlalchemy import delete, select, update

from wots.core.board import InvalidTransition
from wots.core.models import Employee, Suppression, WorkItem
from wots.core.offices import OfficeError
from wots.core.repo import NotFound, TenancyViolation, scoped

from .conftest import as_user, employee_id, fake_team, internal, new_item


@pytest.fixture
def two_offices(make_runtime):
    rt = make_runtime()
    other = rt.offices.create_office("Rival Studio", templates=["web_agency"], ceo_email="ceo@rival.test")
    fake_team(rt, other)
    return rt, internal(rt), other


@pytest.mark.parametrize("statement", [
    lambda: select(WorkItem),
    lambda: select(Employee).where(Employee.name == "Pixel"),
    lambda: update(WorkItem).values(priority=5),
    lambda: delete(Suppression),
])
def test_queries_without_an_org_filter_fail(make_runtime, statement):
    rt = make_runtime()
    with rt.sessions() as s, pytest.raises(TenancyViolation):
        s.execute(statement())


def test_deliberate_cross_office_reads_opt_out(two_offices):
    rt, _, _ = two_offices
    with rt.sessions() as s:
        rows = s.scalars(select(Employee).execution_options(cross_org=True)).all()
    assert len({e.org_id for e in rows}) == 2


def test_another_office_cant_see_or_change_items(two_offices):
    rt, mine, theirs = two_offices
    item = new_item(rt, ctx=mine, name="Secret Client")
    assert [i.business_name for i in rt.board.items(theirs)] == []
    with pytest.raises(NotFound):
        rt.board.get(theirs, item)
    with pytest.raises(NotFound):
        rt.board.transition(theirs, item, "VERIFY", "system", "test")
    assert not rt.board.claim(theirs, item, "intruder")
    rt.board.release(theirs, item, "intruder")
    rt.board.hold(theirs, item, "intruder", 999)  # an update filtered by the wrong office touches nothing
    assert rt.board.get(mine, item).claimed_by is None
    with pytest.raises(NotFound):
        rt.board.log(theirs, item, "system", "x", "hello")
    assert rt.board.history(theirs, item) == []


def test_the_ceo_of_one_office_cant_act_in_another(two_offices):
    from wots.dashboard import actions

    rt, mine, theirs = two_offices
    item = new_item(rt, ctx=mine)
    rival_ceo = as_user(rt, theirs)
    with pytest.raises(NotFound):
        actions.disqualify(rt.board, rival_ceo, item, "spite")
    with pytest.raises(PermissionError):
        rt.offices.user_ctx(rival_ceo.user_id, mine.slug)  # not a member


def test_another_office_cant_see_or_change_employees(two_offices):
    rt, mine, theirs = two_offices
    pixel = employee_id(rt, mine, "Pixel")
    assert pixel not in {e.id for e in rt.offices.employees(theirs)}
    with pytest.raises(NotFound):
        rt.offices.fire(theirs, pixel)
    with pytest.raises(NotFound):
        rt.offices.update_employee(theirs, pixel, {"max_wip": 5})
    assert {st.info.id for st in rt.atlas.staff(theirs)}.isdisjoint({st.info.id for st in rt.atlas.staff(mine)})


def test_atlas_never_assigns_across_offices(two_offices):
    from .conftest import force_status

    rt, mine, theirs = two_offices
    item = new_item(rt, ctx=theirs)
    force_status(rt, theirs, item, "ASSETS_READY")
    rt.atlas.tick()
    assigned = rt.board.get(theirs, item).assigned_employee_id
    assert assigned in {e.id for e in rt.offices.employees(theirs)}
    assert assigned not in {e.id for e in rt.offices.employees(mine)}


def test_files_are_per_office(two_offices):
    rt, mine, theirs = two_offices
    item = new_item(rt, ctx=mine)
    (rt.files.item_dir(mine.org_id, item) / "copy.json").write_text("{}")
    assert rt.files.resolve(mine.org_id, item, "copy.json").is_file()
    assert not rt.files.resolve(theirs.org_id, item, "copy.json").exists()
    with pytest.raises(PermissionError):
        rt.files.resolve(theirs.org_id, item, f"../../../{mine.org_id}/items/{item}/copy.json")


def test_suppression_lists_are_per_office(two_offices):
    rt, mine, theirs = two_offices
    with rt.sessions.begin() as s:
        s.add(Suppression(org_id=mine.org_id, email="no@thanks.test", reason="unsubscribed"))
    with rt.sessions() as s:
        assert s.scalars(scoped(Suppression, theirs)).all() == []
        assert [r.email for r in s.scalars(scoped(Suppression, mine))] == ["no@thanks.test"]
        s.execute(delete(Suppression).where(Suppression.org_id == theirs.org_id))
        s.commit()
        assert len(s.scalars(scoped(Suppression, mine)).all()) == 1


def test_one_ceo_per_office(two_offices):
    rt, mine, _ = two_offices
    someone = rt.offices.ensure_user("second@ceo.test")
    with pytest.raises(OfficeError):
        rt.offices.add_member(mine, someone.id, "ceo")
    rt.offices.add_member(mine, someone.id, "manager")  # other roles are fine


def test_workflow_rules_still_apply_inside_an_office(two_offices):
    rt, _, theirs = two_offices
    item = new_item(rt, ctx=theirs)
    with pytest.raises(InvalidTransition):
        rt.board.transition(theirs, item, "APPROVED", "system", "test")
