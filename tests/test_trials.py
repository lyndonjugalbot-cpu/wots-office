"""Trials: run the sample leads with changed employee settings in a separate database, then compare."""
import json

import pytest

from wots.core.trials import apply_overrides, compare, parse_overrides, run_trial

from .conftest import config_for, employee_id, internal
from .test_phase1_pipeline import ScriptedClaude


def test_overrides_change_one_employees_config(make_runtime):
    rt = make_runtime()
    ctx = internal(rt)
    apply_overrides(rt, ctx, parse_overrides(["Quill.model=claude-sonnet-5", "Quill.effort=low"]))
    quill = next(e for e in rt.offices.employees(ctx) if e.name == "Quill")
    assert quill.config["model"] == "claude-sonnet-5" and quill.config["effort"] == "low"
    apply_overrides(rt, ctx, parse_overrides(["Quill.effort=default"]))
    assert "effort" not in next(e for e in rt.offices.employees(ctx) if e.id == employee_id(rt, ctx, "Quill")).config


@pytest.mark.parametrize("bad", [
    "Quill.effort=extreme",          # not a level
    "Quill.model=claude-mystery-9",  # no price, so the budget guard couldn't cost it
    "Quill.temperature=0.2",         # not a setting
    "Nobody.model=claude-opus-5",
    "Quill.model",
])
def test_bad_overrides_are_rejected(make_runtime, bad):
    rt = make_runtime()
    with pytest.raises(ValueError):
        apply_overrides(rt, internal(rt), parse_overrides([bad]))


@pytest.mark.e2e
def test_trials_run_and_compare(tmp_path, web):
    config = config_for(tmp_path)
    config.settings.qa = config.settings.qa.model_copy(update={"run_lighthouse": False, "check_external_links": False})
    a = run_trial("opus", [], config=config, llm_client=ScriptedClaude(), echo=lambda *_: None,
                  http_transport=web.transport)
    b = run_trial("sonnet-low", ["Quill.model=claude-sonnet-5", "Quill.effort=low"], config=config,
                  llm_client=ScriptedClaude(), echo=lambda *_: None,
                  http_transport=web.transport)
    assert {l["status"] for l in a["leads"]} == {"READY_FOR_APPROVAL"} == {l["status"] for l in b["leads"]}
    # Same tokens from the scripted client, so the price difference is exactly the model's pricing
    quill_a = a["leads"][0]["by_agent"]["Quill"]["cost"]
    quill_b = b["leads"][0]["by_agent"]["Quill"]["cost"]
    assert quill_b < quill_a and b["models"]["Quill"] == {"model": "claude-sonnet-5", "effort": "low"}
    page = compare(["opus", "sonnet-low"], config).read_text()
    assert "sonnet-low" in page and "claude-sonnet-5 @low" in page and "Harbour Line Plumbing" in page
    with pytest.raises(ValueError):
        run_trial("opus", [], config=config, llm_client=ScriptedClaude(), echo=lambda *_: None,
                  http_transport=web.transport)  # name taken
    assert json.loads((tmp_path / "data" / "trials" / "opus" / "summary.json").read_text())["total_cost"] > 0
