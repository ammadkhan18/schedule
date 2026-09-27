"""
The estimator copilot: a small tool-using agent.

The LLM (free Groq model) never calculates or invents quantities. It can only
call the tools below, which run the deterministic take-off engine. Any change
to the project is returned as a PROPOSAL that the user applies (or not) with a
button - with undo.

Without an AI key, `answer_offline()` routes common requests (explain, what if,
check, what's assumed, material-saving options) to the same tools.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import config
from copilot import analysis as A
from copilot.state import ROOM_TYPES, ProjectState, apply_changes, build, run

MAX_STEPS = 6
TOOL_MODELS = [getattr(config, "GROQ_TOOL_MODEL", "openai/gpt-oss-120b"), "llama-3.3-70b-versatile"]

# Tool schemas are kept SHORT on purpose: they are re-sent on every step and free tiers limit tokens per minute.
CHANGE_SCHEMA = {  # fields per change type are listed in the system prompt (keeps every request small)
    "type": "object",
    "properties": {"type": {"type": "string", "enum": ["set_param", "reset_param", "set_option", "update_room", "add_room",
                                                       "remove_room", "update_opening", "add_opening"]}},
    "required": ["type"],
}


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props or {},
                                                            "required": required or []}}}


_S = {"type": "string"}
TOOLS = [
    _fn("get_project_summary", "Floors, rooms, structure, options, main material totals."),
    _fn("list_rooms", "Rooms with floor, type, size.", {"floor": _S}),
    _fn("list_openings", "Door/window groups with sizes and qty."),
    _fn("list_inputs", "Counts & dimensions with value, source, confidence.", {"group": _S, "only_assumed": {"type": "boolean"}}),
    _fn("explain", "How a quantity was calculated. what = Mat_ID, words, or steel/cement/bricks/sand/crush/tiles/paint/wiring.",
        {"what": _S}, ["what"]),
    _fn("find_material", "Search materials by words.", {"query": _S}, ["query"]),
    _fn("check_takeoff", "Problems, odd ratios, missing items, with fixes."),
    _fn("most_important_questions", "Assumed inputs that change quantities most."),
    _fn("compare_what_if", "Effect of changes WITHOUT applying them.", {"changes": {"type": "array", "items": CHANGE_SCHEMA}},
        ["changes"]),
    _fn("propose_changes", "Prepare changes for the user to Apply. Use for any change request.",
        {"changes": {"type": "array", "items": CHANGE_SCHEMA}, "reason": _S}, ["changes"]),
    _fn("material_saving_options", "Try alternatives and rank by material saved."),
    _fn("lookup_database", "Search material notes/specs and engineering coefficients.", {"query": _S}, ["query"]),
]

SYSTEM_PROMPT = f"""You are the CostLens estimator copilot for 5-10 marla houses in Pakistan (Islamabad/Rawalpindi practice).
You help a homeowner or contractor understand and improve a MATERIAL TAKE-OFF (quantities only - prices are not part of the app yet).

Rules:
- Every number you state must come from a tool result in this conversation. Never estimate or invent quantities.
- To change anything (rooms, sizes, counts, options) call propose_changes. The user must press Apply - never say a change is done.
- For "what if" questions use compare_what_if and report the effect on the main materials in %.
- Change types: set_param(key,value) reset_param(key) set_option(name,value) update_room(room,floor?,length_ft?,width_ft?,room_type?,nth?)
  add_room(room,floor,room_type,length_ft,width_ft) remove_room(room) update_opening(name,width_ft?,height_ft?,qty?) add_opening(...).
- Units: feet, sft, cft, rft, bags, tons, Nos. Room types: {', '.join(ROOM_TYPES)}.
- Options: finish_tier Economy/Standard/Premium, roof_system Traditional/Insulated, masonry Brick/Block, gas_source SNGPL/LPG/None,
  rcc_mix MX_RCC124 (1:2:4) / MX_RCC1153 (1:1.5:3), include_false_ceiling, include_rwh, seismic_bands (true/false).
- Useful input keys: H_FLOOR, H_PLINTH, H_PARAPET, T_SLAB_IN, N_AC, N_LIGHT, N_SK13, N_FAN, N_WC, N_BATH, N_FT, N_GEYSER,
  COL_N, BEAM_N, BOUNDARY_LEN, EXT_EXPOSED_FRAC, UGT_L, UGT_W. Use list_inputs to see all.
- Safety: never suggest removing seismic bands, earthing or waterproofing to save material; mention structural changes need an engineer.
- Answer briefly in plain language (the user's language - English, Urdu or Roman Urdu). Use short bullet points for numbers.
- If asked about prices/costs: say pricing is coming later and answer in quantities."""


@dataclass
class Proposal:
    id: str
    changes: List[dict]
    descriptions: List[str]
    problems: List[str]
    diff: dict
    reason: str = ""
    status: str = "pending"  # pending | applied | dismissed


@dataclass
class AgentReply:
    text: str
    proposals: List[Proposal] = field(default_factory=list)
    tool_log: List[dict] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# tool execution (shared by the LLM agent and the offline router)
# ---------------------------------------------------------------------------
class Toolbox:
    def __init__(self, state: ProjectState):
        self.state = state
        self._res = None
        self.proposals: List[Proposal] = []

    @property
    def res(self):
        if self._res is None:
            self._res = run(self.state)
        return self._res

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        fn: Optional[Callable] = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**(args or {}))
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:  # noqa: BLE001 - report to the model instead of crashing
            return {"error": f"{name} failed: {exc}"}

    # ---- read tools
    def t_get_project_summary(self):
        return A.summarize(self.res)

    def t_list_rooms(self, floor: str = ""):
        return [{"floor": r["Floor"], "room": r["Room"], "type": r["Room type"], "size": f"{r['Length (ft)']:g}x{r['Width (ft)']:g}",
                 "source": r.get("Source", "")} for r in (self.state.rooms or []) if not floor or r["Floor"] == floor]

    def t_list_openings(self):
        return [{"name": o["Name"], "kind": o["Kind"], "size": f"{o['Width (ft)']:g}x{o['Height (ft)']:g}", "qty": o["Qty"],
                 "source": o.get("Source", "")} for o in (self.state.openings or [])]

    def t_list_inputs(self, group: str = "", only_assumed: bool = False):
        p = build(self.state)
        return [{"key": x.key, "label": x.label, "value": round(x.value, 2), "unit": x.unit, "confidence": x.confidence,
                 "source": x.source[:80]} for x in sorted(p.params.values(), key=lambda x: (x.group, x.key))
                if (not group or x.group.lower() == group.lower()) and (not only_assumed or x.confidence in ("Assumed", "Low"))][:60]

    def t_explain(self, what: str):
        return A.explain(self.res, what)

    def t_find_material(self, query: str):
        return A.find_materials(self.res, query)

    def t_check_takeoff(self):
        return [{"severity": f.severity, "title": f.title, "detail": f.detail[:300], "fix": f.fix_label} for f in
                A.check_takeoff(self.state, self.res)]

    def t_most_important_questions(self):
        return [{"input": s.label, "key": s.key, "current": f"{s.value:g} {s.unit}", "source": s.source,
                 "effect_of_change": ", ".join(s.drivers) or "small", "question": A.QUESTION_BANK.get(s.key, ("",))[0]}
                for s in A.sensitivity(self.state, 6)]

    def t_material_saving_options(self):
        return [{"option": o["option"], "main_materials_saved": [f"{k['material']} {k['change_pct']}%" for k in o["saves"]],
                 "main_materials_added": [f"{k['material']} +{k['change_pct']}%" for k in o["adds"]],
                 "other_items_reduced": o["reduced_items"], "note": o["note"], "changes": o["changes"]}
                for o in A.material_saving_options(self.state)]

    def t_lookup_database(self, query: str):
        from knowledge import load_knowledge_base
        kb = load_knowledge_base()
        words = [w for w in (query or "").lower().split() if len(w) > 2]
        hits = []
        for m in kb.materials.values():
            text = f"{m.description} {m.specification} {m.notes} {m.standard} {m.calc_basis}".lower()
            sc = sum(w in text for w in words)
            if sc:
                hits.append((sc, {"mat_id": m.mat_id, "material": m.description, "spec": m.specification,
                                  "standard": m.standard, "notes": m.notes[:250]}))
        for cid, meta in kb.coefficient_meta.items():
            text = f"{cid} {meta['description']} {meta['basis']} {meta['source']}".lower()
            sc = sum(w in text for w in words)
            if sc:
                hits.append((sc, {"coefficient": cid, "description": meta["description"], "value": round(kb.coefficients[cid], 4),
                                  "unit": meta["unit"], "basis": meta["basis"][:200], "source": meta["source"][:150]}))
        hits.sort(key=lambda x: -x[0])
        return [h for _s, h in hits[:6]] or {"result": "nothing found"}

    # ---- what-if / changes
    def t_compare_what_if(self, changes: List[dict]):
        s2, descs, probs = apply_changes(self.state, changes)
        if not descs:
            return {"error": "no valid change", "problems": probs}
        return {"changes": descs, "problems": probs, **A.diff_results(self.res, run(s2), top=6)}

    def t_propose_changes(self, changes: List[dict], reason: str = ""):
        s2, descs, probs = apply_changes(self.state, changes)
        diff = A.diff_results(self.res, run(s2), top=6) if descs else {}
        prop = Proposal(uuid.uuid4().hex[:8], changes, descs, probs, diff, reason)
        self.proposals.append(prop)
        return {"proposal_id": prop.id, "changes": descs, "problems": probs,
                "effect": diff.get("key_totals", []), "note": "Shown to the user with an Apply button - not applied yet."}


def _compact(obj, limit=1800) -> str:
    s = json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + '..."(truncated)"'


# ---------------------------------------------------------------------------
# LLM loop
# ---------------------------------------------------------------------------
def _targets(api_key: str, keys, client) -> List[tuple]:
    """(label, client, model) in order: Groq models first, then Gemini / OpenRouter if the user has keys."""
    if client is not None:  # injected (tests)
        return [("injected", client, m) for m in TOOL_MODELS]
    out = []
    if api_key:
        from ai.groq_client import get_client
        g = get_client(api_key)
        out += [("Groq", g, m) for m in TOOL_MODELS]
    if keys is not None:
        from ai.llm import compat_client, provider_specs
        specs = provider_specs()
        for name in ("gemini", "openrouter"):
            k = getattr(keys, name, "")
            if k:
                out.append((specs[name].label, compat_client(specs[name], k), specs[name].tool_model))
    return out


def run_agent(api_key: str, state: ProjectState, history: List[dict], user_text: str, client=None,
              max_steps: int = MAX_STEPS, keys=None) -> AgentReply:
    """history: previous turns as [{"role": "user"|"assistant", "content": str}] (short).
    Tries Groq, then Gemini / OpenRouter (if keys are given) when a provider is out of its free limit."""
    from ai.llm import friendly_error, is_rate_limited, is_too_large, retry_after_seconds

    box = Toolbox(state)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-4:] + [{"role": "user", "content": user_text}]
    targets = _targets(api_key, keys, client)
    if not targets:
        return AgentReply("", [], [], error="No AI key - add a free Groq or Gemini key in the sidebar.")
    log: List[dict] = []
    last_exc: Optional[Exception] = None
    for _label, cl, model in targets:
        msgs = list(messages)
        waited = False
        step = 0
        try:
            while step < max_steps:
                try:
                    resp = cl.chat.completions.create(model=model, messages=msgs, tools=TOOLS, tool_choice="auto",
                                                      temperature=0.1, max_tokens=1000)
                except Exception as exc:  # noqa: BLE001
                    wait = retry_after_seconds(exc)
                    if is_rate_limited(exc) and not is_too_large(exc) and not waited and wait is not None and wait <= 20:
                        time.sleep(wait + 0.5)  # a short per-minute limit: wait once, then continue
                        waited = True
                        continue
                    raise
                step += 1
                msg = resp.choices[0].message
                calls = getattr(msg, "tool_calls", None) or []
                if not calls:
                    return AgentReply((msg.content or "").strip() or "Done.", box.proposals, log)
                msgs.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [{"id": c.id, "type": "function",
                                             "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                                            for c in calls]})
                for c in calls:
                    try:
                        args = json.loads(c.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                        result = {"error": "arguments were not valid JSON"}
                    else:
                        result = box.call(c.function.name, args)
                    log.append({"tool": c.function.name, "args": args, "result": result})
                    msgs.append({"role": "tool", "tool_call_id": c.id, "content": _compact(result)})
            return AgentReply("I needed more steps than allowed for this request. Here is what I prepared so far"
                              + (" - see the proposed change below." if box.proposals else "."), box.proposals, log)
        except Exception as exc:  # noqa: BLE001 - try the next model / provider, then report
            last_exc = exc
            continue
    msg = friendly_error(last_exc) if last_exc else "The AI copilot could not be reached."
    return AgentReply("", box.proposals, log, error=msg)


# ---------------------------------------------------------------------------
# offline router (no AI key) - same tools, fixed phrasing
# ---------------------------------------------------------------------------
WHAT_IF_WORDS = [
    (r"\bblock", [{"type": "set_option", "name": "masonry", "value": "Block"}], "block walls"),
    (r"\bbrick wall|\bbrick\b", [{"type": "set_option", "name": "masonry", "value": "Brick"}], "brick walls"),
    (r"insulat", [{"type": "set_option", "name": "roof_system", "value": "Insulated"}], "an insulated roof"),
    (r"traditional roof|mud|brick tile", [{"type": "set_option", "name": "roof_system", "value": "Traditional"}], "a traditional roof"),
    (r"economy|basic finish", [{"type": "set_option", "name": "finish_tier", "value": "Economy"}], "economy finishes"),
    (r"premium", [{"type": "set_option", "name": "finish_tier", "value": "Premium"}], "premium finishes"),
    (r"1\s*:\s*1\.5\s*:\s*3|3000 ?psi", [{"type": "set_option", "name": "rcc_mix", "value": "MX_RCC1153"}], "1:1.5:3 concrete"),
    (r"no false ceiling|without false ceiling", [{"type": "set_option", "name": "include_false_ceiling", "value": False}],
     "no false ceilings"),
    (r"\blpg\b", [{"type": "set_option", "name": "gas_source", "value": "LPG"}], "LPG gas"),
]


def answer_offline(state: ProjectState, text: str) -> AgentReply:
    box = Toolbox(state)
    t = (text or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')?\s*(?:floor height|storey height|story height)", t) or \
        re.search(r"(?:floor|storey|story) height (?:of |to |= )?(\d+(?:\.\d+)?)", t)
    if m and ("what if" in t or "change" in t or "make" in t or "set" in t):
        return _whatif_reply(box, [{"type": "set_param", "key": "H_FLOOR", "value": float(m.group(1))}], f"{m.group(1)} ft floor height")
    m = re.search(r"(\d+)\s*(?:acs?|air ?conditioners?)\b", t)
    if m and ("what if" in t or "only" in t or "change" in t or "set" in t):
        return _whatif_reply(box, [{"type": "set_param", "key": "N_AC", "value": int(m.group(1))}], f"{m.group(1)} ACs")
    if "what if" in t or "switch" in t or "instead" in t or "compare" in t:
        for pat, ch, label in WHAT_IF_WORDS:
            if re.search(pat, t):
                return _whatif_reply(box, ch, label)
    if re.search(r"sav|reduce|cheaper|less material|value engineer", t):
        opts = box.t_material_saving_options()
        if not opts:
            return AgentReply("I tried the usual alternatives but none reduces the main materials for this house.")
        lines = [f"- **{o['option']}** - saves " + (", ".join(o["main_materials_saved"]) or ", ".join(o["other_items_reduced"]) or "a little")
                 + (f"; adds {', '.join(o['main_materials_added'])}" if o["main_materials_added"] else "") for o in opts]
        for o in opts[:3]:
            box.t_propose_changes(o["changes"], reason=o["option"])
        return AgentReply("Material-saving options for this house (best first):\n" + "\n".join(lines)
                          + "\n\nThe top options are ready below - press **Apply** to try one (you can undo).", box.proposals)
    if re.search(r"check|review|anything wrong|mistake|problem", t):
        fs = box.t_check_takeoff()
        if not fs:
            return AgentReply("I found nothing unusual in this take-off.")
        icon = {"problem": "\U0001f534", "check": "\U0001f7e0", "info": "\u2139\ufe0f"}
        return AgentReply("Take-off check:\n" + "\n".join(f"- {icon.get(f['severity'], '')} **{f['title']}** - {f['detail'][:160]}"
                                                          for f in fs[:8]) + "\n\nFixes can be applied in the *Take-off check* panel.")
    if re.search(r"assum|default|confirm|important question|uncertain", t):
        qs = box.t_most_important_questions()
        if not qs:
            return AgentReply("The inputs that matter most are already confirmed.")
        return AgentReply("Confirm these first - they change the quantities most:\n" + "\n".join(
            f"- **{q['input']}** (now {q['current']}, {q['source'][:50]}): effect {q['effect_of_change']}" for q in qs)
            + "\n\nAnswer them under *Most important questions* in Step 3.")
    words = [w for w in re.findall(r"[a-z]+", t) if w in A.GROUP_WORDS]
    mid = re.search(r"\b([a-z]{2,3}-\d{3})\b", t)
    if mid or words or re.search(r"why|how|explain|kaise|kyun", t):
        what = mid.group(1).upper() if mid else (words[0] if words else re.sub(r"\b(why|how|explain|is|the|so|much|many|do|we|need|of)\b", "", t).strip())
        e = box.t_explain(what)
        if "error" in e:
            return AgentReply(f"I couldn't find '{what}'. Try a Mat_ID (e.g. CON-001) or: steel, cement, bricks, sand, crush, tiles, paint, wiring.")
        return AgentReply(render_explanation(e))
    return AgentReply(
        "Without an AI key I understand these requests:\n- *explain steel* / *why 955 bags of cement?* / *explain CON-001*\n"
        "- *what if block walls?* / *what if insulated roof?* / *what if 12 ft floor height?* / *what if only 4 ACs?*\n"
        "- *check my take-off* - *what is assumed?* - *how can I save material?*\n"
        "Add a free Groq key in the sidebar to ask anything in your own words (English or Urdu) and to edit rooms by chat.")


def _whatif_reply(box: Toolbox, changes: List[dict], label: str) -> AgentReply:
    r = box.t_compare_what_if(changes)
    if "error" in r:
        return AgentReply(f"That change isn't possible: {'; '.join(r.get('problems', []))}")
    box.t_propose_changes(changes, reason=f"What if {label}")
    return AgentReply(f"**What if {label}** - effect on the main materials:\n" + render_key_diff(r["key_totals"])
                      + "\n\nPress **Apply** below to use it (you can undo).", box.proposals)


def render_key_diff(rows: List[dict]) -> str:
    moved = [r for r in rows if abs(r["change_pct"]) >= 0.5]
    if not moved:
        return "- No noticeable change in the main materials."
    return "\n".join(f"- {r['material']}: {r['before']} → {r['after']} ({r['change_pct']:+.1f}%)" for r in moved)


def render_explanation(e: dict) -> str:
    if "group" in e:
        lines = [f"**{e['group']}: {e['total']}** in total, made up of:"]
        lines += [f"- {p['work_item'].split(' ', 1)[1]}: {p['share_pct']:.0f}% ({p['work_qty']} of work)" for p in e["parts"][:6]]
        if e["items"]:
            lines.append("\n**Buy:** " + "; ".join(f"{i['material']} - {i['buy'] or i['qty_incl_wastage']}" for i in e["items"][:5]))
        return "\n".join(lines)
    lines = [f"**{e['material']}** ({e['mat_id']}, {e['spec']}): net {e['net_qty']:,.2f} {e['unit']} + {e['wastage_pct']}% "
             f"wastage = **{e['qty_incl_wastage']:,.2f} {e['unit']}** ({e['buy']}). Status: {e['status']}."]
    for p in e["parts"]:
        lines.append(f"- {p['share_pct']:.0f}% from *{p['work_item'].split(' ', 1)[1]}*: {p['work_qty']} x {p['coefficient']} "
                     f"= {p['material_qty']:,.2f} ({p['how_measured'][:120]})")
    if not e["parts"] and e.get("calculation"):
        lines.append(f"- {e['calculation']}")
    return "\n".join(lines)
