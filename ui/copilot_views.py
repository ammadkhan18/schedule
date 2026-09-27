"""
Streamlit UI for the agentic features:

* render_smart_questions()  - Step 3: "Most important questions" (sensitivity-ranked, one click to apply)
* render_checker(res)       - Step 4: take-off check with one-click fixes
* render_copilot(res)       - Step 4: chat copilot (Groq tools, or offline router), proposals with
                              Apply / Dismiss, undo history and named scenarios

Every change goes through copilot.state.apply_changes (validated) and is
written to the same session keys the review tables use, with an undo snapshot.
"""
from __future__ import annotations

from typing import List

import pandas as pd
import streamlit as st

from copilot import analysis as A
from copilot.agent import AgentReply, Proposal, answer_offline, render_key_diff, run_agent
from copilot.state import ProjectState, apply_changes

SEV_ICON = {"problem": "\U0001f534", "check": "\U0001f7e0", "info": "\u2139\ufe0f"}


# ---------------------------------------------------------------------------
# session <-> state
# ---------------------------------------------------------------------------
def state_from_session() -> ProjectState:
    from ui import mto_views
    return ProjectState(
        pi=st.session_state["project_inputs"], params=st.session_state["extracted_params"],
        rooms=st.session_state.get("dmto_rooms"), openings=st.session_state.get("dmto_openings"),
        overrides=dict(st.session_state.get("dmto_overrides") or {}), options=st.session_state["dmto_options"],
        floors=st.session_state.get("dmto_floors"), facts=st.session_state.get("package_facts"),
        scan=st.session_state.get("dmto_scan"), mode=mto_views.drawing_mode())


def _write_state(s: ProjectState) -> None:
    st.session_state["dmto_rooms"] = s.rooms
    st.session_state["dmto_openings"] = s.openings
    st.session_state["dmto_overrides"] = dict(s.overrides)
    st.session_state["dmto_options"] = s.options
    st.session_state["dmto_ver"] = st.session_state.get("dmto_ver", 0) + 1  # refresh review tables
    st.session_state["dmto_counts_ver"] = st.session_state.get("dmto_counts_ver", 0) + 1


def apply_to_session(changes: List[dict], label: str) -> tuple:
    """Validate + apply changes, keep an undo snapshot. Returns (descriptions, problems)."""
    from ui import mto_views
    if st.session_state.get("step") == 3:
        mto_views.commit_review_from_widgets()  # keep unsaved Rooms / Doors & windows table edits
    cur = state_from_session()
    new, descs, probs = apply_changes(cur, changes)
    if not descs or any("cannot" in p or "not possible" in p or "zero" in p for p in probs):
        return descs, probs or ["Nothing to change."]
    undo = st.session_state.setdefault("copilot_undo", [])
    undo.append({"label": label, "snapshot": cur.snapshot()})
    del undo[:-15]  # keep the last 15 steps
    _write_state(new)
    return descs, probs


def undo_last() -> str:
    undo = st.session_state.get("copilot_undo") or []
    if not undo:
        return ""
    last = undo.pop()
    _write_state(state_from_session().restore(last["snapshot"]))
    return last["label"]


# ---------------------------------------------------------------------------
# Step 3: most important questions
# ---------------------------------------------------------------------------
def render_smart_questions() -> None:
    try:
        state = state_from_session()
        sens = A.sensitivity(state, top_n=6)
    except Exception as exc:  # never block Step 3
        st.caption(f"Smart questions unavailable: {exc}")
        return
    if not sens:
        st.success("\U0001f3af The inputs that affect the quantities most are already confirmed.")
        return
    with st.expander(f"\U0001f3af Most important questions ({len(sens)}) - answer these first", expanded=True):
        st.caption("These inputs are still defaults and move your main materials the most (ranked). "
                   "Answer what you know; skip the rest.")
        answers = {}
        for s in sens:
            q, kind, choices = A.QUESTION_BANK.get(s.key, (f"{s.label} ({s.unit})?", "number", []))
            c1, c2 = st.columns([3, 2])
            now = f"{s.value:g} {s.unit if s.unit != '-' else ''}"
            if kind == "choice":
                match = [lbl for lbl, v in choices if not isinstance(v, tuple) and abs(float(v) - s.value) < 0.05]
                now = match[0] if match else ("4' x 5' (assumed)" if s.key == A.WINDOW_TEST else now)
            with c1:
                st.markdown(f"**{q}**  \n<span style='color:#6b7280;font-size:0.85em'>Now: {now}"
                            f" ({s.source[:60]}) · affects {', '.join(s.drivers) or 'several items'}</span>",
                            unsafe_allow_html=True)
            with c2:
                key = f"sq_{s.key}_{st.session_state.get('dmto_counts_ver', 0)}"
                if kind == "choice":
                    labels = ["(keep)"] + [lbl for lbl, _v in choices]
                    pick = st.selectbox(q, labels, key=key, label_visibility="collapsed")
                    if pick != "(keep)":
                        answers[s.key] = dict(choices)[pick]
                else:
                    presets = ["(keep)"] + [f"{c:g}" for c in choices] + ["Other..."]
                    pick = st.selectbox(q, presets, key=key, label_visibility="collapsed")
                    if pick == "Other...":
                        answers[s.key] = st.number_input("Value", value=float(s.value), key=key + "_n", label_visibility="collapsed")
                    elif pick != "(keep)":
                        answers[s.key] = float(pick)
        if st.button("Apply my answers", type="primary", disabled=not answers, key="sq_apply"):
            changes = []
            state = state_from_session()
            for k, v in answers.items():
                changes += A.changes_for_answer(state, k, v)
            descs, probs = apply_to_session(changes, "Answers to the most important questions")
            if probs:
                st.error("; ".join(probs))
            else:
                st.session_state["copilot_flash"] = "Applied: " + "; ".join(descs)
                st.rerun()
    flash = st.session_state.pop("copilot_flash", None)
    if flash:
        st.success(flash)


# ---------------------------------------------------------------------------
# Step 4: take-off check
# ---------------------------------------------------------------------------
def render_checker(res) -> None:
    try:
        findings = A.check_takeoff(state_from_session(), res)
    except Exception as exc:
        st.caption(f"Take-off check unavailable: {exc}")
        return
    if not findings:
        st.success("\U0001f50e Take-off check: nothing unusual found.")
        return
    n_prob = sum(f.severity == "problem" for f in findings)
    n_chk = sum(f.severity == "check" for f in findings)
    title = f"\U0001f50e Take-off check - {n_prob} problem(s), {n_chk} thing(s) to check"
    with st.expander(title, expanded=n_prob > 0):
        for i, f in enumerate(findings):
            c1, c2 = st.columns([5, 2])
            c1.markdown(f"{SEV_ICON.get(f.severity, '')} **{f.title}**  \n{f.detail}")
            if f.changes and f.fix_label:
                if c2.button(f.fix_label, key=f"fix_{i}_{abs(hash(f.title)) % 10**6}", width="stretch"):
                    descs, probs = apply_to_session(f.changes, f"Fix: {f.title}")
                    if probs and not descs:
                        st.error("; ".join(probs))
                    else:
                        st.session_state["copilot_flash4"] = "Applied: " + "; ".join(descs) + " - take-off recalculated."
                        st.rerun()
    flash = st.session_state.pop("copilot_flash4", None)
    if flash:
        st.success(flash)


# ---------------------------------------------------------------------------
# Step 4: copilot chat
# ---------------------------------------------------------------------------
QUICK = [
    ("Explain the steel", "Explain the steel"),
    ("Why this much cement?", "Why this much cement?"),
    ("Check my take-off", "Check my take-off"),
    ("What is assumed?", "What is assumed?"),
    ("How can I save material?", "How can I save material?"),
    ("What if block walls?", "What if block walls?"),
]


def _ask(text: str) -> None:
    msgs = st.session_state.setdefault("copilot_msgs", [])
    msgs.append({"role": "user", "content": text})
    from ai.llm import keys_from_mapping
    keys = keys_from_mapping(st.session_state)
    state = state_from_session()
    if keys.any():
        history = [{"role": m["role"], "content": m["content"]} for m in msgs[:-1] if m.get("content")][-4:]
        with st.spinner("Copilot is working on it..."):
            reply = run_agent(keys.groq, state, history, text, keys=keys)
        if reply.error:
            off = answer_offline(state, text)
            reply = AgentReply(f"_{reply.error} Answering with the built-in assistant:_\n\n" + off.text, off.proposals)
    else:
        reply = answer_offline(state, text)
    props = st.session_state.setdefault("copilot_props", {})
    for p in reply.proposals:
        props[p.id] = p
    msgs.append({"role": "assistant", "content": reply.text, "proposals": [p.id for p in reply.proposals],
                 "tools": [t["tool"] for t in reply.tool_log]})


def _render_proposal(p: Proposal) -> None:
    with st.container(border=True):
        st.markdown(f"**Proposed change{'s' if len(p.descriptions) > 1 else ''}**" + (f" - {p.reason}" if p.reason else ""))
        for d in p.descriptions:
            st.markdown(f"- {d}")
        if p.diff.get("key_totals"):
            st.markdown(render_key_diff(p.diff["key_totals"]))
        blocking = [x for x in p.problems if "cannot" in x or "not possible" in x or "zero" in x]
        for x in p.problems:
            (st.error if x in blocking else st.warning)(x)
        if p.status == "pending":
            c1, c2 = st.columns(2)
            if c1.button("\u2705 Apply", key=f"ap_{p.id}", type="primary", disabled=bool(blocking) or not p.descriptions):
                descs, probs = apply_to_session(p.changes, p.reason or "; ".join(p.descriptions)[:80])
                p.status = "applied" if descs else "failed"
                st.rerun()
            if c2.button("Dismiss", key=f"dm_{p.id}"):
                p.status = "dismissed"
                st.rerun()
        else:
            st.caption({"applied": "\u2705 Applied - take-off recalculated", "dismissed": "Dismissed"}.get(p.status, p.status))


def render_copilot(res) -> None:
    from ai.llm import keys_from_mapping
    has_key = keys_from_mapping(st.session_state).any()
    st.caption(("\U0001f916 AI copilot (free models): ask in your own words, English or Urdu. " if has_key else
                "\U0001f916 Built-in copilot (no AI key): use the buttons or short requests like *what if block walls?*. "
                "Add a free Groq key in the sidebar to ask anything and edit rooms by chat. ")
               + "It only uses the take-off engine for numbers and never changes anything until you press **Apply**.")
    cols = st.columns(3)
    for i, (label, text) in enumerate(QUICK):
        if cols[i % 3].button(label, key=f"q_{i}", width="stretch"):
            _ask(text)
            st.rerun()

    props = st.session_state.get("copilot_props", {})
    for m in st.session_state.get("copilot_msgs", [])[-12:]:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("tools"):
                st.caption("Used: " + ", ".join(dict.fromkeys(m["tools"])))
            for pid in m.get("proposals", []):
                if pid in props:
                    _render_proposal(props[pid])
    text = st.chat_input("Ask the copilot - e.g. 'make the master bedroom 14x15', 'what if 12 ft floor height?'", key="copilot_input")
    if text:
        _ask(text)
        st.rerun()

    c1, c2, c3 = st.columns(3)
    undo = st.session_state.get("copilot_undo") or []
    if c1.button(f"\u21a9\ufe0f Undo last change ({len(undo)})", disabled=not undo, width="stretch"):
        lbl = undo_last()
        st.session_state["copilot_msgs"] = st.session_state.get("copilot_msgs", []) + [
            {"role": "assistant", "content": f"Undone: {lbl}."}]
        st.rerun()
    if c2.button("Clear chat", width="stretch", disabled=not st.session_state.get("copilot_msgs")):
        st.session_state["copilot_msgs"] = []
        st.rerun()
    with c3.popover("\U0001f4be Scenarios", width="stretch"):
        _render_scenarios()


def _render_scenarios() -> None:
    sc = st.session_state.setdefault("copilot_scenarios", {})
    name = st.text_input("Save the current inputs as", value=f"Option {chr(65 + len(sc))}", key="sc_name")
    if st.button("Save scenario", key="sc_save"):
        sc[name] = state_from_session().snapshot()
        st.rerun()
    if sc:
        base = state_from_session()
        named = {"Current": base, **{n: base.restore(s) for n, s in sc.items()}}
        st.dataframe(pd.DataFrame(A.scenario_table(named)), hide_index=True)
        pick = st.selectbox("Switch to scenario", list(sc), key="sc_pick")
        c1, c2 = st.columns(2)
        if c1.button("Use this scenario", key="sc_use"):
            cur = state_from_session()
            st.session_state.setdefault("copilot_undo", []).append({"label": f"Switch to scenario {pick}", "snapshot": cur.snapshot()})
            _write_state(cur.restore(sc[pick]))
            st.rerun()
        if c2.button("Delete", key="sc_del"):
            sc.pop(pick, None)
            st.rerun()
