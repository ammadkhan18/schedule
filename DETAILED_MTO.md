# Detailed Material Schedule (Master Material Database)

Since v0.5.0 the whole wizard runs on this engine: Step 1 sets the take-off
specification, Step 2 reads the drawings, Step 3 reviews rooms / doors &
windows / counts / structure, Step 4 shows the material take-off and Step 5
exports it. Costs are intentionally excluded in this version.

## How it works

```
Drawings (vector PDF) ──► package_analyzer (existing)  ─┐
                     └──► detailed_mto/text_scanner    ─┤
Step 3 verified parameters ─────────────────────────────┤
Room_Finish_Defaults (knowledge base) ──────────────────┘
                              │
                              ▼
            detailed_mto/builder.py  → DetailedProject (every input with source + confidence)
                              │
                              ▼
     detailed_mto/quantities.py (101 work items)   +   Recipes (coefficients) from the Excel DB
                              │
                              ▼
            detailed_mto/engine.py → all 309 materials, each with a status
                              │
                              ▼
            detailed_mto/export.py → Excel workbook (10 sheets, live formulas, blank rates)
```

**Material quantity = Σ (work-item quantity × recipe coefficient) × (1 + wastage).**
Materials that are counts, lump sums or stand-alone areas use
`detailed_mto/direct.py`.

### What is read from the drawings (no AI)

| Source | Facts |
|---|---|
| `package_analyzer` (existing) | rooms with sizes per floor, wall lengths by thickness (9" / 4.5"), footprint and perimeter, levels (plinth, floor height, slab), strip foundation width and depth, column sizes, door/window totals, area statement, plot size, drawing conflicts |
| `text_scanner` (new) | plumbing labels on plumbing sheets (F.T, M.H, G.T, C.O, VANITY, CONCEALED WC, SHOWER AREA, SUMP), door/chogath schedule rows (size, chogath width, single/double, qty), overhead tank capacity, septic / UG / OH tank sizes, WARDROBE / BALCONY / TERRACE / DB labels |

Everything else (electrical point counts, finishes, window widths when not
given) comes from `Room_Finish_Defaults` and is marked **Assumed** so the user
can correct it in the review tables.

### Status of every line

| Status | Meaning |
|---|---|
| Calculated | from drawing / verified inputs |
| Calculated (assumed inputs) | at least one input is a default - verify |
| Provisional | owner-supplied / PC items (e.g. split AC units, decorative lights) |
| Counted elsewhere (reference) | assembly or duplicate line, quantity shown for information only |
| Option - not included | optional/alternative material; "If-selected Qty" shows what it would be (tick "Quantify optional items" in Step 1 to include) |
| Needs input | cannot be calculated without a user value |
| Not required / Not in scope | zero for this project / outside selected scope |

## The Excel export

* **Summary** (opens first): main materials at a glance, how firm the numbers
  are, and a **shopping list of every material to buy** grouped by trade
  (use the +/- buttons to open/close a trade), in purchase units, with the
  construction stage and a reliability flag (✔ from drawings / ⚠ check).
* Only materials inside the selected **scope** are exported.
* Every sheet has a "◄ Summary" link, filter/sort buttons and colour-coded
  status; click a Mat_ID to jump between the schedule and its calculation.
* Wastage % (blue) is editable in Material_Schedule; qty incl. wastage, buy
  quantities, the Summary and the benchmarks recalculate.

## Editing the knowledge base

`data/master_material_database.xlsx` is the single source of truth. Edit
coefficients (blue cells), wastage, mixes, room defaults or add materials
in Excel, save, then run:

```
python scripts/validate_knowledge_base.py
```

The loader evaluates the workbook's own formulas in Python (it does not rely
on cached Excel values), so edits take effect on the next app run. Keep
`Mat_ID`, `WI_ID` and `Coeff_ID` values stable - they are the keys.

To add a new work item: add it to `Work_Items`, add its recipe lines in
`Recipes`, and add a calculator function decorated with `@wi("WI-XX-NN")`
in `detailed_mto/quantities.py`.

## Accuracy notes

* Areas, finishes, doors, plumbing fixtures: close to measured (±2-10%) on
  vector CAD PDFs.
* Masonry / plaster: ±5-10% (wall-pair detection).
* Steel: ratio method (±15-30%) until a structural BBS is provided.
* Electrical wiring/conduit, pipe lengths: per-point allowances (±15-25%).
* Scanned drawings give no vector facts; the schedule then relies on Step 3
  values and defaults (flagged as assumed).

The Benchmarks sheet flags cement, steel, bricks, sand and crush ratios per
sft that fall outside typical ranges.

## Main files

```
data/master_material_database.xlsx   knowledge base (21 sheets)
knowledge/loader.py                  Excel loader + formula evaluator + integrity checks
detailed_mto/model.py                project model with provenance + take-off options
detailed_mto/text_scanner.py         label scan (plumbing, door schedule, tanks)
detailed_mto/builder.py              app state + drawing facts + reviewed rows -> project model
detailed_mto/quantities.py           101 work-item calculators
detailed_mto/direct.py               non-recipe material calculators
detailed_mto/engine.py               roll-up, statuses, scope/options, RCC mix
detailed_mto/edits.py                review-table row conversions
detailed_mto/export.py               Excel export (quantities only)
ui/mto_views.py                      Step 2-5 views
scripts/validate_knowledge_base.py   DB validation CLI
tests/test_detailed_mto.py           17 tests
```


## Guided route (no drawings)

`detailed_mto/brief.py` holds the ProjectBrief (plot, structure, rooms, services, outside) and turns it
into the engine's inputs: room rows, door/window rows, concept floors and parameter overrides.
`ai/sketch_reader.py` asks the free Groq vision/text model for a fixed JSON reading of the sketch and
description plus follow-up questions; `parse_description()` is the rule-based fallback without a key.
`complete_programme()` adds rooms every house needs. The UI is `ui/brief_views.py` (Step 2 in sketch mode).

Concept floor geometry: covered area = room areas x 1.28; wall centre-line length = (sum of room perimeters
+ external perimeter) / 2; 25 % of internal walls 9" in load-bearing houses (15 % in RCC frames) - calibrated on
the 31-sheet 5 marla set (total wall length within 1 %). Tune these in `_floor_geometry()` as more real projects
are compared.


## Copilot (agentic features)

```
copilot/state.py     ProjectState (the reviewed inputs) + validated change application (pure, testable)
copilot/analysis.py  key totals & diffs, explain, sensitivity ranking, question bank, take-off checker,
                     material-saving options, scenario table - all deterministic, no AI needed
copilot/agent.py     tool definitions + Groq tool-calling loop (max 6 steps, fallback model),
                     offline router for common requests when there is no key
ui/copilot_views.py  Step 3 "Most important questions", Step 4 "Take-off check" and "Copilot" tab,
                     proposals (Apply / Dismiss), undo, scenarios
```

Design rules: the LLM never produces quantities - it calls tools that run the engine; every change is a
proposal validated by the input checks and applied only by the user (with an undo snapshot); tool outputs
are compact so free-tier requests stay small. Tests (`tests/test_copilot.py`) use a scripted fake LLM client
to cover tool calls, bad arguments, unknown tools, step limits and API failures. The model is set in
`config.GROQ_TOOL_MODEL` (fallback `llama-3.3-70b-versatile`).
