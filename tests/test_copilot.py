"""Tests for the agentic slice: state changes, analysis, checker, smart questions, tool-calling loop (mocked LLM)."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from ai.extraction import default_building_params
from copilot import analysis as A
from copilot.agent import Toolbox, answer_offline, run_agent
from copilot.state import ProjectState, apply_changes, build, run
from detailed_mto import build_project
from detailed_mto.edits import openings_to_rows, rooms_to_rows
from engineering import plot_templates
from knowledge import load_knowledge_base
from models.schemas import ProjectInputs


@pytest.fixture(scope="module")
def state():
    kb = load_knowledge_base()
    pi = ProjectInputs(project_name="copilot", plot_marla=5)
    params = plot_templates.build_template_params(pi) or default_building_params()
    p = build_project(pi, params, kb)
    return ProjectState(pi, params, rooms_to_rows(p), openings_to_rows(p), {}, p.options, None, None, None, "none")


def test_apply_changes_and_validation(state):
    room = state.rooms[0]["Room"]
    s2, descs, probs = apply_changes(state, [
        {"type": "update_room", "room": room, "floor": state.rooms[0]["Floor"], "length_ft": 20},
        {"type": "add_room", "room": "store", "floor": "ground", "room_type": "Store / utility", "length_ft": 6, "width_ft": 5},
        {"type": "set_option", "name": "masonry", "value": "Block"},
        {"type": "set_param", "key": "N_AC", "value": 3},
        {"type": "set_option", "name": "masonry", "value": "Mud"},       # invalid choice
        {"type": "remove_room", "room": "does not exist"}])               # unknown room
    assert len(descs) == 4 and len(probs) == 2
    assert any(r["Room"] == "STORE" for r in s2.rooms) and s2.options.masonry == "Block" and s2.overrides["N_AC"] == 3
    assert state.options.masonry != "Block" and "N_AC" not in state.overrides  # original untouched
    _s3, _d, probs = apply_changes(state, [{"type": "set_param", "key": "N_WC", "value": -1}])
    assert any("negative" in p for p in probs)


def test_undo_snapshot_roundtrip(state):
    snap = state.snapshot()
    s2, _, _ = apply_changes(state, [{"type": "set_option", "name": "roof_system", "value": "Insulated"}])
    back = s2.restore(snap)
    assert back.options.roof_system == state.options.roof_system


def test_what_if_diff_and_group_explanation(state):
    base = run(state)
    s2, _, _ = apply_changes(state, [{"type": "set_option", "name": "masonry", "value": "Block"}])
    d = A.diff_results(base, run(s2))
    bricks = next(k for k in d["key_totals"] if k["material"] == "Bricks")
    assert bricks["change_pct"] < -30
    e = A.explain(base, "steel")
    assert e["group"] == "Steel" and e["parts"] and abs(sum(p["share_pct"] for p in e["parts"]) - 100) < 5
    e2 = A.explain(base, "CON-001")
    assert e2["mat_id"] == "CON-001" and e2["parts"]


def test_sensitivity_ranks_only_uncertain_inputs(state):
    sens = A.sensitivity(state, top_n=8)
    assert sens and all(s.confidence in ("Assumed", "Low") for s in sens)
    assert sens == sorted(sens, key=lambda x: -x.impact)
    # answering a question turns it into a user value and it drops off the list
    k = sens[0].key
    q = A.QUESTION_BANK[k]
    ans = q[2][0][1] if q[1] == "choice" else (q[2][1] if len(q[2]) > 1 else sens[0].value)
    s2, descs, probs = apply_changes(state, A.changes_for_answer(state, k, ans))
    assert descs and not probs
    assert k not in [x.key for x in A.sensitivity(s2, top_n=8)]


def test_checker_finds_and_fixes_inconsistency(state):
    s2, _, _ = apply_changes(state, [{"type": "set_param", "key": "N_FT", "value": 0}])
    res = run(s2)
    fs = A.check_takeoff(s2, res)
    ft = [f for f in fs if "floor traps" in f.title]
    assert ft and ft[0].severity == "problem" and ft[0].changes
    s3, _, probs = apply_changes(s2, ft[0].changes)
    assert not probs and not [f for f in A.check_takeoff(s3, run(s3)) if "floor traps" in f.title]


def test_material_saving_options(state):
    opts = A.material_saving_options(state)
    assert opts and opts[0]["score"] >= opts[-1]["score"]
    assert any("block" in o["option"].lower() for o in opts)


def test_offline_router(state):
    assert "Steel" in answer_offline(state, "explain steel").text
    r = answer_offline(state, "what if block walls?")
    assert r.proposals and "Bricks" in r.text
    r = answer_offline(state, "what if 12.5 ft floor height")
    assert r.proposals and r.proposals[0].changes[0]["value"] == 12.5
    assert "Take-off check" in answer_offline(state, "check my take-off").text or "nothing unusual" in answer_offline(state, "check").text
    assert "understand" in answer_offline(state, "hello").text


# ---------------------------------------------------------------- mocked LLM tool loop
def _tool_call(i, name, args):
    return NS(id=f"call_{i}", type="function", function=NS(name=name, arguments=json.dumps(args)))


class FakeClient:
    """Scripted Groq client: returns the given messages in order, recording what it was sent."""
    def __init__(self, script):
        self.script, self.sent = list(script), []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.sent.append(kw)
        msg = self.script.pop(0)
        return NS(choices=[NS(message=msg)])


def test_agent_loop_explain_and_propose(state):
    room = state.rooms[0]
    script = [
        NS(content="", tool_calls=[_tool_call(1, "explain", {"what": "cement"})]),
        NS(content="", tool_calls=[_tool_call(2, "propose_changes", {"changes": [
            {"type": "update_room", "room": room["Room"], "floor": room["Floor"], "length_ft": 15}], "reason": "bigger room"})]),
        NS(content="Cement comes mostly from slabs. I prepared the room change - press Apply.", tool_calls=None),
    ]
    client = FakeClient(script)
    reply = run_agent("key", state, [], "why so much cement, and make the first room 15 ft long", client=client)
    assert not reply.error and "Apply" in reply.text
    assert [t["tool"] for t in reply.tool_log] == ["explain", "propose_changes"]
    assert reply.proposals and reply.proposals[0].descriptions and not reply.proposals[0].problems
    # tool results were sent back to the model
    tool_msgs = [m for m in client.sent[-1]["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 2 and "Cement" in tool_msgs[0]["content"]
    assert client.sent[0]["tools"] and client.sent[0]["tool_choice"] == "auto"
    # nothing was applied to the state
    assert state.rooms[0]["Length (ft)"] == room["Length (ft)"]


def test_agent_handles_bad_tool_args_and_unknown_tools(state):
    script = [NS(content="", tool_calls=[NS(id="c1", type="function", function=NS(name="explain", arguments="{not json"))]),
              NS(content="", tool_calls=[_tool_call(2, "delete_everything", {})]),
              NS(content="Sorry, I could not do that.", tool_calls=None)]
    reply = run_agent("key", state, [], "do something odd", client=FakeClient(script))
    assert reply.text.startswith("Sorry") and "error" in reply.tool_log[0]["result"] and "error" in reply.tool_log[1]["result"]


def test_agent_step_limit_and_api_failure(state):
    loop = [NS(content="", tool_calls=[_tool_call(i, "get_project_summary", {})]) for i in range(20)]
    reply = run_agent("key", state, [], "loop", client=FakeClient(loop), max_steps=3)
    assert "more steps" in reply.text

    class Boom:
        chat = NS(completions=NS(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("429 rate limit"))))
    reply = run_agent("key", state, [], "hi", client=Boom())
    assert reply.error and "limit" in reply.error  # friendly rate-limit message


def test_toolbox_read_tools(state):
    box = Toolbox(state)
    assert box.call("get_project_summary", {})["key_totals"]["Cement"].endswith("bags")
    assert box.call("list_rooms", {"floor": "ground"})
    assert box.call("lookup_database", {"query": "bricks per cft"})
    assert "error" in box.call("explain", {"wrong_arg": 1})


def test_agent_reports_bad_key(state):
    class Unauthorized:
        chat = NS(completions=NS(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("Error code: 401 - invalid api key"))))
    reply = run_agent("key", state, [], "hi", client=Unauthorized())
    assert "key was rejected" in reply.error
