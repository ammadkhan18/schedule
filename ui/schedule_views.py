"""
Step 4 tab "Schedule & Gantt": construction programme estimated from the take-off.

All scheduling logic lives in scheduling/; this module only renders and collects the
user's settings (start date, working days, holidays, productivity, gangs, rates, durations).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import List, Optional

import altair as alt
import pandas as pd
import streamlit as st

import config
from scheduling.engine import PHASES, WEEKDAY_NAMES, Schedule, ScheduleSettings, build_schedule, material_delivery_plan
from scheduling.export import build_schedule_workbook, schedule_csv
from scheduling.productivity import default_rate

CRIT_COLOR, NORMAL_COLOR, FLOAT_COLOR = "#C0392B", config.BRAND_TEAL, "#CBD5E1"
SCHEDULE_WIDGET_KEYS = ["sched_start", "sched_days", "sched_hol", "sched_prod", "sched_chain", "sched_ftg", "sched_col",
                        "sched_dsh", "sched_dry", "sched_view", "sched_color", "sched_float", "sched_lead", "sched_ver"]


def settings() -> ScheduleSettings:
    s = st.session_state.get("sched_settings")
    if not isinstance(s, ScheduleSettings):
        s = ScheduleSettings(start_date=(date.today() + timedelta(days=7)).isoformat())
        st.session_state["sched_settings"] = s
    return s


def schedule_for(res) -> Optional[Schedule]:
    """Schedule of the current take-off with the current settings (cached per result + settings)."""
    if res is None:
        return None
    s = settings()
    key = (id(res), repr(s))
    cache = st.session_state.get("sched_cache")
    if cache and cache[0] == key:
        return cache[1]
    sched = build_schedule(res, s)
    st.session_state["sched_cache"] = (key, sched)
    return sched


def workbook_bytes(res, project_name: str) -> bytes:
    sched = schedule_for(res)
    lead = int(st.session_state.get("sched_lead", 3) or 3)
    return build_schedule_workbook(sched, project_name, material_delivery_plan(res, sched, lead))


# ---------------------------------------------------------------- settings panel
def _seed(key: str, value) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def _render_settings() -> ScheduleSettings:
    s = settings()
    _seed("sched_start", s.start())
    _seed("sched_days", [WEEKDAY_NAMES[d] for d in s.work_weekdays])
    _seed("sched_hol", "\n".join(s.holidays))
    _seed("sched_prod", int(s.productivity_pct))
    _seed("sched_chain", s.one_gang_per_trade)
    _seed("sched_ftg", s.footing_curing_days)
    _seed("sched_col", s.column_curing_days)
    _seed("sched_dsh", s.deshuttering_days)
    _seed("sched_dry", s.plaster_drying_days)

    c1, c2, c3 = st.columns([1, 2, 1])
    start = c1.date_input("Start date", key="sched_start", format="DD/MM/YYYY")
    days = c2.multiselect("Working days", WEEKDAY_NAMES, key="sched_days",
                          help="Typical Pakistani sites work Monday to Saturday (Sunday off).")
    prod = c3.slider("Site productivity", 50, 150, step=5, key="sched_prod", format="%d%%",
                     help="100% = the listed norms. Lower it for a slow site, summer heat, Ramadan or an "
                          "inexperienced contractor; raise it for a large, well-managed team.")
    with st.expander("⚙️ Calendar, curing & sequencing", expanded=False):
        a1, a2 = st.columns([1, 2])
        hol_txt = a1.text_area("Holidays / no-work days (one date per line, YYYY-MM-DD)", key="sched_hol", height=150,
                               help="Add Eid-ul-Fitr and Eid-ul-Adha breaks (usually 3-7 days each), 14 August, "
                                    "and any other days the site will be closed.")
        with a2:
            chain = st.toggle("One gang per trade (finish a floor before moving to the next)", key="sched_chain",
                              help="On = the same masonry / plaster / tile / electrician / plumber gang goes floor by floor "
                                   "(usual for a small contractor). Off = floors can overlap if you have extra gangs.")
            b1, b2 = st.columns(2)
            ftg = b1.number_input("Footing / plinth-beam curing (calendar days)", 0, 28, key="sched_ftg")
            col = b2.number_input("Slab pour → next-floor columns (calendar days)", 0, 28, key="sched_col")
            dsh = b1.number_input("Slab pour → de-shuttering & masonry below (calendar days)", 7, 28, key="sched_dsh",
                                  help="Props under slabs are normally kept 14 days (ACI/PEC practice 14-21 days).")
            dry = b2.number_input("Plaster curing & drying before putty/paint (calendar days)", 0, 45, key="sched_dry")
    holidays: List[str] = []
    bad = []
    for line in (hol_txt or "").replace(",", "\n").splitlines():
        t = line.strip()
        if not t:
            continue
        try:
            holidays.append(date.fromisoformat(t).isoformat())
        except ValueError:
            bad.append(t)
    if bad:
        st.warning("Ignored holiday entries that are not dates (use YYYY-MM-DD): " + ", ".join(bad[:5]))
    if not days:
        st.warning("Select at least one working day - using Monday to Saturday.")
    new = replace(s, start_date=start.isoformat() if start else "", productivity_pct=float(prod),
                  work_weekdays=[WEEKDAY_NAMES.index(d) for d in days] or [0, 1, 2, 3, 4, 5],
                  holidays=sorted(set(holidays)), one_gang_per_trade=bool(chain), footing_curing_days=int(ftg),
                  column_curing_days=int(col), deshuttering_days=int(dsh), plaster_drying_days=int(dry))
    st.session_state["sched_settings"] = new
    return new


# ---------------------------------------------------------------- gantt
def _gantt(sched: Schedule, level: str, color_by: str, show_float: bool) -> alt.Chart:
    acts = sched.activities
    if level == "Phases":
        rows = [{"Label": name, "Start": pd.Timestamp(s), "End": pd.Timestamp(f + timedelta(days=1)),
                 "Phase": name, "Critical": "Critical" if any(a.critical and a.phase_name == name for a in acts) else "Has float",
                 "Days": sum(1 for d in sched.calendar if s <= d <= f), "Float": 0,
                 "LateEnd": pd.Timestamp(f + timedelta(days=1)), "Floor": "", "Work": ""}
                for name, s, f in sorted(sched.phase_spans())]
    else:
        rows = []
        for i, a in enumerate(acts, 1):
            late = sched.calendar[a.lf] if a.lf < len(sched.calendar) else a.finish
            rows.append({"Label": f"{i:02d}  {a.name}", "Start": pd.Timestamp(a.start),
                         "End": pd.Timestamp(a.finish + timedelta(days=1)), "Phase": a.phase_name,
                         "Critical": "Critical" if a.critical else "Has float", "Days": a.duration,
                         "Float": a.total_float, "LateEnd": pd.Timestamp(late + timedelta(days=1)), "Floor": a.floor,
                         "Work": a.work_text()[:160]})
    df = pd.DataFrame(rows)
    order = list(df["Label"])
    y = alt.Y("Label:N", sort=order, title=None, axis=alt.Axis(labelLimit=380, labelFontSize=11))
    x = alt.X("Start:T", title=None, axis=alt.Axis(format="%b %Y", labelAngle=0, grid=True, tickCount="month", orient="top"))
    tip = [alt.Tooltip("Label:N", title="Activity"), alt.Tooltip("Start:T", title="Start", format="%a %d %b %Y"),
           alt.Tooltip("End:T", title="Ends before", format="%a %d %b %Y"), alt.Tooltip("Days:Q", title="Working days"),
           alt.Tooltip("Float:Q", title="Total float (wd)"), alt.Tooltip("Phase:N")]
    if level != "Phases":
        tip.append(alt.Tooltip("Work:N", title="Work"))
    if color_by == "Critical path":
        color = alt.Color("Critical:N", scale=alt.Scale(domain=["Critical", "Has float"], range=[CRIT_COLOR, NORMAL_COLOR]),
                          legend=alt.Legend(title=None, orient="top"))
    else:
        color = alt.Color("Phase:N", sort=list(PHASES.values()), legend=alt.Legend(title=None, orient="top", columns=3),
                          scale=alt.Scale(scheme="tableau10"))
    height = max(120, 26 * len(df))
    base = alt.Chart(df)
    layers = []
    if show_float and level != "Phases":
        fl = df[df["Float"] > 0]
        if not fl.empty:
            layers.append(alt.Chart(fl).mark_bar(color=FLOAT_COLOR, height=6, opacity=0.9)
                          .encode(y=y, x=alt.X("End:T"), x2="LateEnd:T",
                                  tooltip=[alt.Tooltip("Label:N", title="Activity"), alt.Tooltip("Float:Q", title="Float (wd)")]))
    layers.append(base.mark_bar(cornerRadius=3, height=16).encode(y=y, x=x, x2="End:T", color=color, tooltip=tip))
    today = pd.Timestamp(date.today())
    if sched.start <= date.today() <= sched.finish:
        layers.append(alt.Chart(pd.DataFrame({"t": [today]})).mark_rule(color=config.BRAND_GOLD, strokeWidth=2, strokeDash=[4, 3])
                      .encode(x="t:T"))
    return alt.layer(*layers).properties(height=height)


# ---------------------------------------------------------------- editors
def apply_activity_edits(s: ScheduleSettings, base: pd.DataFrame, edited: pd.DataFrame) -> ScheduleSettings:
    """Turn edits in the activity table into gang / duration overrides (pure - unit tested)."""
    gangs, durs = dict(s.gang_overrides), dict(s.duration_overrides)
    for (_, old), (_, new) in zip(base.iterrows(), edited.iterrows()):
        aid = old["ID"]
        if pd.notna(new["Gangs"]) and float(new["Gangs"]) > 0 and float(new["Gangs"]) != float(old["Gangs"]):
            gangs[aid] = float(new["Gangs"])
            durs.pop(aid, None)  # a new gang count recalculates the duration
        if pd.notna(new["Duration (wd)"]) and int(new["Duration (wd)"]) >= 1 and \
                int(new["Duration (wd)"]) != int(old["Duration (wd)"]):
            durs[aid] = int(new["Duration (wd)"])
    return replace(s, gang_overrides=gangs, duration_overrides=durs)


def apply_rate_edits(s: ScheduleSettings, base: pd.DataFrame, edited: pd.DataFrame) -> ScheduleSettings:
    """Turn edits in the productivity table into rate overrides; a value equal to the default removes the override."""
    rates = dict(s.rate_overrides)
    for (_, old), (_, new) in zip(base.iterrows(), edited.iterrows()):
        v = new["Output per gang-day"]
        if pd.isna(v) or float(v) <= 0 or float(v) == float(old["Output per gang-day"]):
            continue
        if abs(float(v) - float(old["Default"])) < 1e-9:
            rates.pop(old["Key"], None)
        else:
            rates[old["Key"]] = float(v)
    return replace(s, rate_overrides=rates)


def _activity_editor(sched: Schedule) -> None:
    s = settings()
    ver = st.session_state.get("sched_ver", 0)
    rows = [{"#": i, "ID": a.id, "Activity": a.name, "Gangs": float(a.gangs), "Duration (wd)": int(a.duration),
             "Start": a.start, "Finish": a.finish, "Float (wd)": a.total_float, "Critical": "🔴" if a.critical else "",
             "Predecessors": ", ".join(p + (f" +{lag}d" if lag else "") for p, lag in a.preds),
             "How the duration was estimated": a.basis_text()}
            for i, a in enumerate(sched.activities, 1)]
    base = pd.DataFrame(rows)
    with st.form(f"sched_act_form_{ver}", border=False):
        edited = st.data_editor(
            base, key=f"sched_act_editor_{ver}", hide_index=True, width="stretch", height=520,
            disabled=[c for c in base.columns if c not in ("Gangs", "Duration (wd)")],
            column_config={
                "#": st.column_config.NumberColumn(width="small"),
                "ID": st.column_config.TextColumn(width="small"),
                "Activity": st.column_config.TextColumn(width="large"),
                "Gangs": st.column_config.NumberColumn(min_value=0.5, max_value=10, step=0.5, width="small",
                                                       help="Crews working in parallel on this activity"),
                "Duration (wd)": st.column_config.NumberColumn(min_value=1, max_value=365, step=1, width="small",
                                                               help="Type a number to fix the duration yourself"),
                "Start": st.column_config.DateColumn(format="DD MMM YY"),
                "Finish": st.column_config.DateColumn(format="DD MMM YY"),
                "How the duration was estimated": st.column_config.TextColumn(width="large"),
            })
        applied = st.form_submit_button("Apply changes", type="primary")
    reset = st.button("↺ Reset gangs & durations to calculated", key=f"sched_reset_{ver}")
    if applied:
        st.session_state["sched_settings"] = apply_activity_edits(s, base, edited)
        st.session_state["sched_ver"] = ver + 1
        st.rerun()
    if reset:
        st.session_state["sched_settings"] = replace(s, gang_overrides={}, duration_overrides={})
        st.session_state["sched_ver"] = ver + 1
        st.rerun()


def _rates_editor(sched: Schedule) -> None:
    s = settings()
    ver = st.session_state.get("sched_ver", 0)
    seen = {}
    names = {}
    for a in sched.activities:
        names.setdefault(a.template, a.name.split(" - ")[0])
        for c in a.components:
            if c.rate is None:
                continue
            k = f"{a.template}|{c.wi_id}"
            if k not in seen:
                seen[k] = {"Key": k, "Activity type": names[a.template], "Work item": c.wi_id,
                           "Description": c.description, "Unit": c.unit, "Total qty": 0.0,
                           "Output per gang-day": float(c.rate), "Default": default_rate(a.template, c.wi_id)[0],
                           "Gang / basis": c.gang_basis}
            seen[k]["Total qty"] += c.qty
    base = pd.DataFrame(list(seen.values()))
    if base.empty:
        return
    base["Total qty"] = base["Total qty"].round(1)
    st.caption("Output of ONE gang in ONE 8-hour working day, in the BOQ unit. Defaults are typical for private "
               "contractors on 5-10 marla houses in Pakistan (manual excavation, site-mixed concrete, hired steel "
               "shuttering). Replace them with your contractor's figures.")
    with st.form(f"sched_rate_form_{ver}", border=False):
        edited = st.data_editor(
            base, key=f"sched_rate_editor_{ver}", hide_index=True, width="stretch", height=420,
            column_order=["Activity type", "Work item", "Description", "Unit", "Total qty", "Output per gang-day",
                          "Default", "Gang / basis"],
            disabled=[c for c in base.columns if c != "Output per gang-day"],
            column_config={"Output per gang-day": st.column_config.NumberColumn(min_value=0.05, step=1.0, format="%.2f"),
                           "Description": st.column_config.TextColumn(width="large")})
        applied = st.form_submit_button("Apply rates", type="primary")
    reset = st.button("↺ Restore default rates", key=f"sched_rate_reset_{ver}")
    if applied:
        st.session_state["sched_settings"] = apply_rate_edits(s, base, edited)
        st.session_state["sched_ver"] = ver + 1
        st.rerun()
    if reset:
        st.session_state["sched_settings"] = replace(s, rate_overrides={})
        st.session_state["sched_ver"] = ver + 1
        st.rerun()


# ---------------------------------------------------------------- the tab
def render_schedule_tab(res) -> None:
    st.caption("Construction programme estimated from the take-off quantities: each activity's duration = quantity ÷ "
               "(output per gang-day × gangs), linked floor by floor with concrete curing and de-shuttering times. "
               "A planning estimate - agree the final programme with your contractor.")
    _render_settings()
    sched = schedule_for(res)
    if sched is None or not sched.activities:
        st.info("No quantified work items in the selected scope - nothing to schedule.")
        return
    s = sched.settings

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Start", f"{sched.start:%d %b %Y}")
    m2.metric("Completion", f"{sched.finish:%d %b %Y}")
    m3.metric("Duration", f"{sched.calendar_days / 30.4:.1f} months", f"{sched.working_days} working days",
              delta_color="off", delta_arrow="off")
    grey_end = max((a.finish for a in sched.activities if a.phase in ("SUB", "STR")), default=None)
    m4.metric("Grey structure complete", f"{grey_end:%d %b %Y}" if grey_end else "-",
              f"{sum(1 for a in sched.activities if a.critical)} critical activities", delta_color="off",
              delta_arrow="off")
    if s.gang_overrides or s.duration_overrides or s.rate_overrides:
        st.caption(f"✏️ Your edits in use: {len(s.gang_overrides)} gang count(s), {len(s.duration_overrides)} fixed "
                   f"duration(s), {len(s.rate_overrides)} productivity rate(s).")

    g1, g2, g3 = st.columns([1, 1, 1])
    level = g1.radio("Show", ["Activities", "Phases"], horizontal=True, key="sched_view")
    color_by = g2.radio("Colour by", ["Critical path", "Phase"], horizontal=True, key="sched_color")
    show_float = g3.toggle("Show float", value=True, key="sched_float",
                           help="Grey tail = how many working days the activity can slip without delaying completion.")
    chart = _gantt(sched, level, color_by, show_float)
    rows = len(sched.phase_spans()) if level == "Phases" else len(sched.activities)
    # Streamlit fits legend + axis inside the given height, so add headroom for them
    st.altair_chart(chart, width="stretch", height=max(120, 26 * rows) + (150 if color_by == "Phase" else 90))

    crit = sched.critical_path()
    with st.expander(f"🔴 Critical path ({len(crit)} activities) - any delay here delays completion", expanded=False):
        st.markdown("  →  ".join(f"**{a.name}** ({a.duration} d)" for a in crit))

    t1, t2, t3 = st.tabs(["🗂️ Activities (edit gangs / durations)", "⚙️ Productivity rates", "🚚 Material delivery plan"])
    with t1:
        _activity_editor(sched)
    with t2:
        _rates_editor(sched)
    with t3:
        lead = st.number_input("Order lead time (calendar days before the material is needed)", 0, 60, value=3,
                               key="sched_lead")
        rows = material_delivery_plan(res, sched, int(lead))
        st.caption("When each purchased material has to be on site: the start of the first activity that uses it.")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=460,
                     column_order=["Order by", "Needed on site", "Material", "Quantity to buy", "Buy unit", "First used in",
                                   "Trade"],
                     column_config={"Order by": st.column_config.DateColumn(format="DD MMM YY"),
                                    "Needed on site": st.column_config.DateColumn(format="DD MMM YY"),
                                    "Material": st.column_config.TextColumn(width="large"),
                                    "Quantity to buy": st.column_config.NumberColumn(format="localized")})

    pi = st.session_state.get("project_inputs")
    name = ((pi.project_name if pi else "") or "Project")
    fn = name.replace(" ", "_")
    d1, d2 = st.columns(2)
    d1.download_button("📅 Schedule & Gantt workbook (Excel)", data=lambda: workbook_bytes(res, name),
                       file_name=f"{fn}_Schedule.xlsx", type="primary", width="stretch",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="sched_dl_xlsx")
    d2.download_button("Schedule (CSV)", data=schedule_csv(sched), file_name=f"{fn}_Schedule.csv", mime="text/csv",
                       width="stretch", key="sched_dl_csv")
