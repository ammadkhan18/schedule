# CostLens — *From plans to materials.* (v0.8.1)

A drawing-based **Material Take-Off (MTO)** tool for 5-10 marla houses in
Pakistan. Upload the complete drawing set (CAD-exported PDFs work best); the
app reads rooms, walls, levels, foundations, schedules and service labels
directly from the drawings, lets you review them, and quantifies **every
material in the Master Material Database** (309 materials, 101 work items)
with status, confidence, construction stage and full traceability — exported
to Excel.

**Costs are intentionally excluded in v0.5.0.** Pricing will be added as a
separate step once the quantities are right (the legacy cost modules in
`mto_boq/` and `export/` are kept, unused by the UI, for that step).

### Two ways in

* **Architect's drawings** (CAD PDF drawing set) - quantities are read from the drawings.
* **No drawings - a sketch, a photo or just an idea** - the user uploads a hand sketch / photo and/or
  describes the house; the AI (free Groq model, optional) reads it and asks follow-up questions, and the
  app asks six plain-language question groups (plot, structure, rooms, services, finishes, outside).
  The answers become a concept layout and the same full material take-off (concept accuracy about
  ±15-30 %). Without an AI key, the description is read with simple rules and the questions still work.

### Code map

```
app.py                   page setup, sidebar, step router (thin)
ui/steps/                setup.py (1) - analysis.py (2) - review.py (3) - takeoff.py (4) - export.py (5)
ui/sidebar.py            step navigation, project save / open, new project
ui/brief_views.py        Step 2 for users without drawings (sketch + questions)
ui/mto_views.py          review tables, take-off views, stale-result handling
ui/copilot_views.py      most important questions, take-off check, copilot chat, undo, scenarios
detailed_mto/            take-off engine: builder, quantities, engine, validation, brief, exports,
                         shopping list (text/PDF), concept plan (SVG)
copilot/                 agent tools, analysis (sensitivity, checker, what-if), Groq tool loop
persistence/             project files (.costlens.json) and the local project library
knowledge/               Master Material Database loader (data/master_material_database.xlsx)
drawing_processing/      CAD PDF reading (text + vectors), label scan, AI page selection
mto_boq/, export/        legacy cost modules - unused by the UI, kept for the pricing step
```

### Workflow

1. **Project Setup** — plot, units, project details, and the *take-off
   specification* (scope incl. Grey Structure / Finishing packages, finish
   tier, structural RCC mix, roof treatment, walling, gas supply, false
   ceilings, rainwater recharge well, seismic bands, optional items). Upload
   the drawing set.
2. **Drawing Analysis** — free CAD text/vector reading of every sheet + a label
   scan (floor traps, manholes, WCs, vanities, showers, roof outlets, door /
   chogath schedule, tanks). The AI is optional.
3. **Review Data** — tabs for Rooms, Doors & windows, Counts & dimensions,
   Structure and Coefficients. Everything shows where it came from
   (drawing / derived / default / your edit).
4. **Material Take-Off** — material schedule (filter by category / status),
   by construction stage, work-item quantities, traceability per material,
   assumptions & gaps, benchmark checks.
5. **Export** — Excel workbook (10 sheets) and CSV.

See **[DETAILED_MTO.md](DETAILED_MTO.md)** for the engine, knowledge base and
accuracy notes.

> ⚠️ **Disclaimer**: This tool produces **preliminary, indicative**
> quantities and costs for early-stage budgeting only. It is **not** a
> substitute for a licensed structural engineer's design, a detailed BOQ
> prepared from approved drawings, or a certified quantity surveyor's
> estimate. Always get professional verification before using these
> numbers for tendering, construction, or financial decisions.

---

## 0. Branding

The app is styled as **CostLens** (tagline: *"From plans to price."*). All
visual polish is done within plain Streamlit's platform limits - no custom
JS framework, no React components:

- **Native theme** (`.streamlit/config.toml`) sets the base color palette
  (navy text, teal accent, light background) using Streamlit's own
  `[theme]` keys, so it applies before a single line of the app runs.
- **CSS injection** (`ui/theme.py`, `inject_theme()`) layers the "Outfit"
  Google Font, a gradient hero banner, styled buttons/metric-cards/
  expanders, and a branded sidebar - all via `st.markdown(unsafe_allow_html=True)`
  targeting stable `data-testid` selectors, so it degrades gracefully
  instead of breaking if a future Streamlit version renames a CSS class.
- **`st.logo()`** (guarded with `hasattr(st, "logo")` for older Streamlit
  versions) shows the horizontal logo lockup in the sidebar header.
- **Logo assets** (`assets/costlens_icon.png`, `assets/costlens_logo.png`)
  are generated entirely with Pillow - see `assets/generate_logo.py` if you
  ever want to tweak the mark or regenerate it at a different size.

---

## 1. Why this architecture

The single most important design decision in this project:

**The LLM (Groq) never calculates final quantities.**

Vision-language models are good at *reading* a drawing — spotting a grid of
columns, guessing a slab thickness note, reading a dimension string — but
they are unreliable at *arithmetic* and have no memory of engineering
formulas. So this app splits the pipeline in two:

1. **AI interpretation layer** (`ai/`) — Groq's vision-capable LLM looks at
   the drawing image(s) plus any OCR'd text and returns a **structured JSON**
   guess of building parameters (floors, footing type, column count/size,
   beam count/size, slab area/thickness, wall length, openings, etc.),
   each with a **confidence level** (High/Medium/Low) and a short reason.
2. **Deterministic engineering layer** (`engineering/`) — plain Python
   functions implement standard civil-engineering thumb-rule formulas
   (excavation volume, concrete volume, steel-by-thumb-rule, formwork
   contact area, masonry/plaster area, etc.). These functions are pure,
   unit-tested-friendly, and 100% traceable — every number in the final
   BOQ can be traced back to an input parameter and a named formula.

Between steps 1 and 2 sits a **mandatory human-in-the-loop review screen**:
the user sees every AI-extracted parameter, its confidence level, and can
edit any value before a single quantity is calculated. Nothing the LLM says
is trusted blindly.

```
Upload drawing + project inputs
        │
        ▼
PDF/Image processing (PyMuPDF, OpenCV, EasyOCR)
        │
        ▼
Groq Vision LLM  →  structured JSON (ExtractedBuildingParams)
        │                                   with confidence per field
        ▼
User verification / edit screen  (Streamlit)
        │
        ▼
Deterministic engineering calculations (engineering/calculations.py)
        │
        ▼
MTO  →  BOQ (rates + wastage)  →  Cost Estimate
        │
        ▼
Excel (openpyxl) + PDF (ReportLab) export
```

---

## 2. What's included in the MVP

Supports simple **1–2 storey RCC residential buildings** (isolated footings,
rectangular columns, rectangular beams, flat RCC slabs, brick/block infill
walls) and estimates:

- Earthwork excavation to founding depth + PCC (incl. working-space allowance),
  backfilling around footings, and earth filling in the plinth
- Anti-termite treatment & PCC (lean concrete) below footings
- Footing concrete + reinforcement (footing *thickness* and *founding depth*
  are separate inputs)
- Column concrete + reinforcement (incl. the below-ground stub from top of
  footing to ground level)
- Beam concrete + reinforcement (beam depth below the slab only at slab
  levels, so the slab/beam overlap is not double-counted)
- Slab concrete + reinforcement
- Staircase (RCC dog-legged, per slab level incl. roof access) + reinforcement
- Lintels over all openings and chajjas (sunshades) over windows + reinforcement
- Ground-floor base: brick soling + PCC floor base
- Formwork/shuttering contact area (footings, columns, beams, slabs, stairs, lintels)
- Masonry / blockwork: units, mortar (both material) and laying labour,
  openings and lintels deducted, roof parapet added
- Plaster: internal walls, external walls (split using the external
  perimeter) and ceilings, openings deducted
- Flooring (built-up area allowance)
- Roof waterproofing + roof insulation/tiles (mud fill + brick/tuff tiles)
- Painting (internal walls, external walls, ceilings - tied to plaster areas)
- DPC (damp-proof course) at plinth level
- Parametric MEP allowances: electrical works (per m² covered area),
  bathroom plumbing & sanitary (per bathroom), kitchen (per kitchen),
  external water supply & drainage (per house)
- Doors & windows (supply + installation, priced per door / per m² of
  window area, scaled by Finish Level)
- **Cement bags, sand, and aggregate/crush quantities** for every concrete
  pour and every mortar (masonry + plaster) - see section 3d below
- Preliminary reinforcement **allowance** as a % check against thumb-rule
  steel (sanity cross-check, flagged if they diverge a lot)
- "Preliminaries & site overheads" and "Contingency" (both %-based, editable in Step 5)
- Concrete-grade-aware pricing: RCC rates are priced at M20 and PCC at M10;
  other grades adjust the rate by the cement/sand/aggregate cost difference
- Steel-grade-aware quantities: thumb-rule steel is scaled by a (partial)
  grade factor - Grade 40 needs more steel, Grade 75 slightly less
- Input sanity checks before calculation (impossible inputs block the
  calculation; unusual ones are flagged)

Every quantity row carries: **formula name, inputs used, confidence,
assumption notes**, visible in an expandable "Calculation Breakdown" panel.

### Explicitly out of scope for this MVP (documented, not silently dropped)
- Detailed structural design / bar bending schedules
- Detailed MEP design / MEP quantity take-off (points, pipe runs) and HVAC —
  MEP is priced as parametric allowances only (see above)
- Boundary wall, main gate, landscaping, solar/UPS
- Non-RCC structural systems (steel frame, load-bearing masonry without RCC
  frame, sloped/truss roofs)
- Multi-wing / irregular floor plans beyond basic rectangle decomposition
- Seismic/wind design checks

---

## 3. Project structure

```
mto_boq_estimator/
├── app.py                      # Streamlit entrypoint / page router
├── config.py                   # App-wide constants & settings (incl. brand palette)
├── requirements.txt            # pinned, tested versions
├── requirements-ocr.txt        # OPTIONAL EasyOCR (heavy - not for free hosting)
├── requirements-dev.txt        # + pytest
├── .streamlit/
│   └── config.toml             # Native Streamlit theme (colors, base font)
├── assets/
│   ├── generate_logo.py        # Regenerates the CostLens logo PNGs (Pillow-only)
│   ├── costlens_icon.png       # Square mark (favicon / st.logo icon_image)
│   ├── costlens_logo.png       # Horizontal lockup, navy text (for light backgrounds)
│   ├── costlens_logo_on_dark.png # Same lockup, white text (used in the navy sidebar)
│   └── fonts/                  # Outfit (OFL-licensed) - bundled for the logo generator
├── models/
│   └── schemas.py              # Pydantic models (single source of truth)
├── ai/
│   ├── groq_client.py          # Groq API wrapper (vision + text)
│   ├── prompts.py              # System/user prompt templates
│   └── extraction.py           # Orchestrates AI extraction -> Pydantic
├── drawing_processing/
│   ├── pdf_processor.py        # PyMuPDF: PDF -> images, text layer
│   ├── package_analyzer.py     # Drawing-set analysis without AI (sheets, text, vector walls)
│   ├── image_processor.py      # OpenCV: cleanup, deskew, resize
│   └── ocr_engine.py           # EasyOCR wrapper, dimension-text helpers
├── engineering/
│   ├── rules.py                # Thumb-rule constants & default assumptions
│   ├── plot_templates.py       # 5-10 marla plot templates & plausibility ranges
│   ├── calculations.py         # ALL deterministic quantity formulas
│   └── validation.py           # Input sanity checks run before calculation
├── mto_boq/
│   ├── mto_generator.py        # Params -> MTO line items (calls engineering/)
│   ├── boq_generator.py        # MTO -> BOQ (rates, wastage, cost)
│   └── rates.py                # Default material/labour rate book (editable)
├── export/
│   ├── excel_export.py         # openpyxl workbook builder
│   └── pdf_export.py           # ReportLab PDF report builder
├── ui/
│   ├── state.py                # Streamlit session-state helpers
│   ├── components.py           # Reusable UI widgets (editable tables etc.)
│   └── theme.py                # CostLens CSS injection + hero/step-tracker helpers
├── utils/
│   ├── helpers.py              # Small shared utilities
│   └── units.py                # SI <-> FPS conversion, feet-inch formatting, trace localisation
├── data/
│   ├── material_rates.json     # Default rate book (PKR, editable in-app)
│   └── default_assumptions.json# Default engineering assumptions
└── sample_data/                # (optional) sample drawing for demo
```

---

## 3b-0. Plot templates & full drawing sets (5-10 marla)

The app is built for **5-10 marla residential houses in Pakistan** and uses
that scope as knowledge:

**Plot templates (Step 1).** Pick the plot size (5/7/8/10 marla), the marla
standard (**225 sqft** society/LDA-style or **272.25 sqft** traditional - a
~21% difference), storeys (single, G+1, G+2) and frontage. The app builds a
complete typical house for that plot (covered footprint after typical
front/rear open space, column grid with spans <= 14 ft, beams, walls,
doors/windows, bathrooms), so an estimate exists even with no drawing. Every
value is Low confidence and labelled for review (`engineering/plot_templates.py`).

**Plausibility checks.** With a plot chosen, Step 3 flags values that don't
fit the plot (covered area larger than the plot, too few/many columns,
too many storeys or bathrooms).

**Full drawing sets, read without AI (Step 2).** Upload the whole set (up
to 10 files, multi-page PDFs). For CAD-exported PDFs,
`drawing_processing/package_analyzer.py` reads, at zero AI cost:
- **sheet sorting** from titles: floor plans per floor, sections,
  elevations, foundation/structural, site, MEP, schedules (several views
  per page are handled);
- **plans:** room labels with sizes (bathrooms/kitchens counted), and, from
  the vector geometry at the drawing scale, wall lengths (double wall lines,
  openings bridged), footprint and perimeter, 9"/4.5" wall mix, door swings.
  If the scale note is missing, the scale is inferred from the plot width
  (Low confidence);
- **sections:** floor-to-floor height, plinth, parapet, founding depth
  (level marks) and slab thickness;
- **structural sheets:** footing schedule and count, column size and count;
- **title block:** plot size, marla, covered area per floor.

The sheets are cross-checked against each other (floor plans vs section
levels, footings vs columns, stated vs measured area, footprint vs plot
width, drawing marla vs Step 1). Values read from drawings override AI and
template values and carry a "From drawings" note with the page they came
from. Only the most useful pages (ground plan, section, other plans,
elevation, then scans) are sent to the AI, which fills what is still
missing; with no API key, "Continue without AI" still uses everything read
from the drawings.

Scanned drawings and phone photos have no text layer or vectors: they go to
the AI, with the plot template as fallback. Try it with
`sample_data/sample_5_marla_package.pdf` (a CAD-style 5 marla G+1 set;
regenerate with `python sample_data/make_sample_package.py`).

Strip (load-bearing) foundations are supported: choose `strip` as the
footing type in Step 3 (set automatically when the foundation details show
stepped brick wall sections). Width = PCC/trench width, thickness = PCC bed;
the foundation runs under every ground-floor wall.

Limits: CAD styles vary (walls as hatched polygons, blocks, non-rectangular
footprints, scale notes that don't match the plotted size), so measured
values are Medium confidence and always shown for review. Floors with
different covered areas are averaged per floor, which keeps total
quantities correct; a per-floor calculation engine is the next step.

## 3a. Units & currency

**FPS is the default** (Pakistani practice). The Unit System toggle in Step 1
switches the whole project between two complete systems:

- **FPS (Feet-Inch, Pakistani practice, default)** - lengths/heights in feet
  (shown in feet-inch notation such as `10'-0"` in text), member sizes and
  thicknesses in inches (9" walls, 5" slab, 9"x18" columns), areas in sqft,
  volumes in cft, rates per cft/sqft, concrete grades in psi (3000 psi,
  1500 psi PCC), steel as Grade 40/60/75, thumb-rule steel in kg/cft.
  Defaults are true Pakistani round values - a 1,080 sqft plan, 4'-0" x
  4'-0" x 1'-6" footings at 5'-0" founding depth, 10'-0" floor height, 3'x7'
  doors, 4'x4' windows, 6" lintels, 3" PCC, 2'-0" plinth, 3'-0" parapet
  (`engineering/rules.py` `DETAILING["FPS"]`).
- **SI (Metric)** - metres, m², m³, M-grades, Fe-grade steel, kg/m³, with the
  metric defaults (100 m² plan, 1.2 m footings, 150 mm lintels, ...).

In FPS mode **everything** is FPS: Step-3 inputs, calculation traces
(`footing_length_ft`, `working_space_in`, ...), formulas, descriptions,
assumption notes, validation messages, rate remarks, the Step 4/5 tables and
the Excel/PDF exports. Steel is quoted in **kg** and cement in **bags** in both
systems, since that's how they're bought in Pakistan.

Numbers are stored in one canonical base (metres) and converted exactly
(1 ft = 0.3048 m, 1 in = 0.0254 m). In FPS mode, volumes/areas are rounded
in cft/sqft, not in m³ first, so every FPS figure equals a direct feet-inch
hand calculation - `tests/test_fps_system.py` checks this item by item.

Each system uses its own round standard details (e.g. 6" lintel vs 150 mm),
so the same building can differ by a fraction of a percent between the two
(tested to stay under 0.5%). Rates are stored per m³/m² and shown per
cft/sqft in FPS, so `display_quantity × display_rate == amount` always holds.
Switching the unit system mid-project swaps untouched defaults to the new
system's values and clears the previous MTO/BOQ so they are regenerated.

The default rate book (`data/material_rates.json`) is priced in **PKR**,
built from published September-2026 Pakistani market prices for cement,
Grade-60 steel, sand, crush/bajri, bricks, plaster and paint, combined with
standard nominal-mix/dry-volume-factor quantities and typical site labour
allowances (see the `note` field in that file for the full breakdown). As
with everything else in this app, these are **indicative defaults only** -
always override them with your own current, local quotations before
relying on the cost estimate.

---

## 3b. Multi-file drawing upload (Plan / Elevation / Section)

Step 1 accepts up to `config.MAX_DRAWING_FILES` (default 3) separate
drawing files, each taggable as Plan / Elevation / Section / Other. This
matters because a Plan view is a horizontal slice - it cannot show vertical
dimensions at all. Adding a Section lets the AI actually read floor-to-floor
height, footing depth, and slab thickness instead of guessing defaults for
them; an Elevation is a good cross-check for floor count and overall height.
The tag you pick is passed straight into the AI prompt ("Image 1 = Plan,
Image 2 = Section, ...") so the model knows which kind of dimension to
expect from which image.

To stay within Groq's limits, pages/images are capped at
`config.MAX_PAGES_PER_FILE` (default 2) per file and `config.MAX_TOTAL_IMAGES`
(3) in total across every uploaded file - Groq's current vision model,
`qwen/qwen3.8-27b`, accepts at most 3 images per request, and larger requests
are rejected. Extra pages/files beyond the caps are dropped with an on-screen
notice, never a crash.

For vector PDFs (exported from CAD), the PDF's embedded text layer -
dimension strings, notes, schedules - is also passed to the AI as a hint.
This is exact text, far more reliable than reading small raster text.

---

## 3c. Finish Level

The **Finish Level** selector (Step 1: Basic / Standard / Premium) is wired
to the BOQ, not just cosmetic. It never changes a quantity - the same
floor area still gets floored, the same wall area still gets
plastered/painted - it only changes the *rate* applied to the
finish-grade-sensitive categories: **Flooring, Painting, and Plaster**.
Everything else (concrete, steel, excavation, masonry, formwork, etc.) is
unaffected, since finish grade has no bearing on structural work.

`engineering/rules.py::FINISH_LEVEL_RATE_MULTIPLIERS` holds the multipliers
(Standard = 1.00x, matching the rate book's own default rates), derived
from researched September-2026 Pakistani market price spreads for economy
vs. mid-range vs. premium tile, paint, plaster, door, and window finishes.
`Standard` is always the rate book's rate un-modified; `Basic`/`Premium`
scale it up or down. The finish-sensitive categories are Flooring, Painting,
Plaster, Doors, Windows, Electrical, Plumbing & Sanitary and Kitchen. Step 5's BOQ table shows the adjusted rate/amount
directly, and each affected line's Remarks column notes the multiplier
that was applied.

---

## 3d. Procurement quantities: cement, sand, aggregate/crush, doors & windows

The MTO (Step 4) is built to be handed to a supplier, not just a cost
engine's internal working. Two refinements make that possible:

**Cement / sand / aggregate breakdown.** Every concrete pour (PCC,
footings, columns, beams, slabs, DPC) and every cement:sand mortar
(masonry bedding/jointing, internal + external plaster) gets its own
cement-bags / sand-volume / aggregate-volume breakdown immediately below
it in the MTO table, using the standard nominal-mix + dry-volume-factor
method (`engineering/calculations.py::concrete_material_breakdown()` /
`mortar_material_breakdown()`). A project-wide `SUMMARY-CEMENT` /
`SUMMARY-SAND` / `SUMMARY-AGG` rollup (and a "📦 Procurement summary"
metrics row at the top of Step 4) gives the one number that actually goes
to a cement/sand supplier.

These breakdown rows are marked `informational` and **excluded from BOQ
pricing** - their cost is already inside the parent item's composite rate
(e.g. `FTG-CONC-01`'s PKR/m3 rate already covers its own cement, sand,
aggregate, and labour), so pricing them again would double-count the cost.
They're still fully visible in the MTO (Step 4) and in the exported MTO
sheet/section - a checkbox on Step 4 lets you hide them from the on-screen
table if you just want the main structural/finish rows, but they're always
counted in the metrics row and export.

Figures are **net theoretical requirements** - no site wastage/spillage
margin is added, since Step 5's wastage % only applies to priced BOQ
items. Add your own margin when actually ordering (commonly 3-5% for
cement, 5-10% for sand/aggregate).

**Doors & windows.** Previously, door/window count and area were only used
to deduct wall-opening area from the masonry/plaster take-off - they were
never priced. Doors are now a real BOQ line (`DOOR-01`, priced per door)
and windows another (`WINDOW-01`, priced per m² of window area, since
window cost scales with size unlike doors), both scaled by Finish Level
the same way flooring/painting/plaster are.

---

## 4. Local setup

```bash
git clone https://github.com/<your-username>/mto-boq-estimator.git
cd mto-boq-estimator

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

Get a **free** Groq API key at https://console.groq.com/keys and set it as
an environment variable (never commit it):

```bash
export GROQ_API_KEY="gsk_..."          # macOS/Linux
setx GROQ_API_KEY "gsk_..."            # Windows
```

Run the app:

```bash
streamlit run app.py
```

The app also lets a user paste their own Groq API key into a sidebar field
at runtime (kept only in session memory, and used in preference to the
deployment's key) — handy for a shared public demo deployment where you don't
want to spend your own key/quota.

The vision model defaults to `qwen/qwen3.8-27b` with `qwen/qwen3.6-27b`
(deprecated by Groq) as a fallback. When Groq retires a model, set
`GROQ_VISION_MODEL` / `GROQ_VISION_MODEL_FALLBACK` as a secret or environment
variable - no code change needed. Transient errors (429/5xx) are retried with
backoff.

Run the tests:

```bash
pip install -r requirements-dev.txt
pytest
```

---

## 5. Deployment (100% free stack)

See **`DEPLOYMENT.md`** for the full step-by-step guide to:
1. Push this project to GitHub
2. Deploy on Streamlit Community Cloud (free tier)
3. Configure the `GROQ_API_KEY` secret
4. Common troubleshooting (package sizes, EasyOCR model downloads, etc.)

---

## 6. Engineering assumptions & thumb rules used

All defaults live in `engineering/rules.py` (mirrored in
`data/default_assumptions.json`). The steel thumb rules, PCC thickness, plinth
height, excavation working space and parapet height are **editable in Step 3**
("Engineering assumptions"); rates, wastage, preliminaries and contingency are
editable in Step 5. Key thumb rules (typical South Asian - Pakistani/Indian -
residential RCC practice, adjust per local code/practice):

| Item | Thumb rule | Notes |
|---|---|---|
| Founding depth | 1.5 m (default) | ground level to underside of footing - drives excavation |
| Footing thickness | 0.45 m (default) | pad thickness - drives footing concrete |
| Footing steel | 60–100 kg/m³ of footing concrete | isolated footing, editable |
| Column steel | 140–200 kg/m³ of column concrete | incl. main bars + ties |
| Beam steel | 110–160 kg/m³ of beam concrete | |
| Slab steel | 70–100 kg/m³ of slab concrete | one-way/two-way not distinguished in MVP |
| Stair / lintel steel | 80–120 / 60–100 kg/m³ | editable |
| Steel grade factor | Grade 40 ×1.25, Grade 60 ×1.00, Grade 75 ×0.93 | partial scaling, indicative |
| Excavation working space | +150 mm each side of footing | for shuttering/working |
| PCC thickness | 75 mm (default) | below footings |
| Concrete wastage | 3–5% | editable |
| Steel wastage | 3–5% | editable |
| Block/brick wastage | 5% | editable |
| Brick count | wall volume ÷ nominal unit volume (incl. 10 mm joint) | 500/m³ modular, ~408/m³ 9"×4.5"×3" |
| Masonry mortar | wall volume − bricks × actual brick volume | ≈0.23 m³ wet mortar per m³ |
| Plaster thickness | 12 mm internal / 15–20 mm external | editable |
| Dry volume factor (wet→dry concrete) | 1.54 | standard factor for mix design qty |
| Dry volume factor (wet→dry mortar) | 1.33 | lower than concrete's - no coarse aggregate to bulk it up |
| Masonry mortar mix | 1:6 (cement:sand) | bedding/jointing |
| Plaster mortar mix | 1:4 (cement:sand) | internal & external |
| Cement bag / bulk density | 50 kg per bag @ 1440 kg/m³ loose | used to convert cement volume → bags |

These are **industry-typical preliminary/thumb-rule values**, not a
substitute for structural design. They are shown to the user with sources
in-app and are fully editable before the BOQ is generated.

---

## 7. Tech stack (all free/open-source)

- **Streamlit** — UI & app hosting (Community Cloud free tier)
- **Groq API** — free-tier LLM inference (vision-capable Llama model) for
  drawing interpretation
- **PyMuPDF (fitz)** — PDF parsing/rasterization
- **OpenCV** — image cleanup (deskew, contrast, resize)
- **EasyOCR** — *optional* OCR pass to help the LLM read dimension text
  (install `requirements-ocr.txt`; too heavy for free Streamlit hosting)
- **Pandas** — tabular data handling
- **Pydantic** — schema validation for all AI outputs and calculation results
- **OpenPyXL** — Excel export
- **ReportLab** — PDF export
- **SQLite** (optional, `utils/helpers.py` has a stub) — local project
  history persistence, disabled by default on Streamlit Cloud (ephemeral
  filesystem)

---

## 7b. Material take-off engine (v0.5.0)

Steps 3-5 are driven by the Master Material Database
(`data/master_material_database.xlsx`, 309 materials, 101 work items, 308 recipe
lines). See **[DETAILED_MTO.md](DETAILED_MTO.md)**.

---

## 8. Changelog

**v0.8.1 — works within free AI limits**
- Fixes Groq free-tier `413 Request too large ... input tokens per minute (ITPM)`: every AI request is
  measured before sending and the drawing pages are resized (then reduced in number) to fit
  `config.GROQ_REQUEST_TOKEN_BUDGET` (5,500 tokens). A typical 3-page request now goes at 1024 px (~5.4k
  tokens instead of ~8.3k).
- 413 -> shrink and retry; 429 "try again in Xs" -> short wait and retry; other failures -> next model.
- Optional extra free providers in the sidebar: **Google Gemini** (large free limits, good vision) and
  **OpenRouter** free models, used automatically when Groq's limit is reached (or instead of Groq).
  Keys stay in the browser session only.
- Copilot requests are ~35 % smaller (compact tool definitions, shorter history, trimmed tool results).
- New module `ai/llm.py` (budgeting, error handling, OpenAI-compatible client); 7 new tests.

**v0.8.0 — save/open projects, sharing, speed, refactor**
- **Projects:** download a project file (.costlens.json - plain JSON, optionally with the drawings) and
  open it later exactly as it was (rooms, edits, drawing facts, options, scenarios, chat); a local project
  library (save / open / delete).
- **Sharing:** shopping list as WhatsApp-ready text (optionally only some trades, e.g. per supplier),
  one-click "Open in WhatsApp", and a PDF shopping list.
- **Speed:** drawing analysis shows a per-sheet progress bar and is cached by file content (re-uploading
  the same set is instant).
- **Sketch route:** schematic concept plan per floor to check the rooms before calculating.
- **Easier Step 3:** the technical tables are labelled "Advanced"; tabs remember the selection.
- **Copilot:** clear messages for the free AI limit and for a rejected key.
- **Traceability:** every Excel export states the app version and the material-database version + hash.
- **Refactor:** app.py (1,000 lines) split into one module per step + sidebar; analyzer gets a progress hook.
- Known limit (needs real project data): RCC-frame plot templates give high steel on 5-7 marla
  (16-20 columns, 5'x5'x1'-6" footings); the take-off check flags it with the reason.

**v0.7.0 — agentic copilot (first slice)**
- **Most important questions** (Step 3): every assumed input is tested against the take-off; only the
  ones that move the main materials most are asked, in plain language with one-click answers.
- **Take-off check** (Step 4): after every calculation - unusual ratios with the reason (e.g. which
  work items drive the steel), inconsistencies (floor traps vs baths, columns in a load-bearing house,
  missing stairs/kitchen, oversized mumty), assumed windows/electrical, drawing conflicts; one-click fixes.
- **Copilot** tab (Step 4): explain any material or group ("why 955 bags of cement?"), what-if
  comparisons, material-saving options, and edits by chat ("make the master bedroom 14x15"). With a
  free Groq key it is a tool-using agent (it can only call the take-off engine - it never invents
  numbers); without a key a built-in assistant handles the common requests.
- Every change is a proposal with its effect on the main materials -> Apply / Dismiss; undo history;
  named scenarios compared side by side.

**v0.6.0 — guided route for users without drawings**
- Step 1 asks "What do you have?": architect's drawings, or a sketch / photo / idea.
- New Step 2 "Your House Requirements": upload a sketch or photo, describe the house, let the AI read
  it (vision model) and answer its follow-up questions, then six simple question groups. Missing
  essentials (stairs, kitchen, a bath per floor) are added automatically and shown.
- The brief becomes rooms, doors & windows, concept floors (walls from shared room edges, calibrated
  on a real 5 marla set) and the user's answers (AC rooms, tanks, sewer, boundary, corner plot ...).
- Concept results are clearly labelled in the app and the Excel.

**v0.5.2 — trustworthy results**
- Scanned drawings / photos are detected: a clear warning in Steps 2-5 and in
  the Excel, the continue button says "typical-house values", and no line is
  marked as calculated from drawings (everything is "assumed" until verified).
- Edits in Step 3 → Counts & dimensions now flow into every derived value
  (e.g. floor height → wall heights, building height, pipe risers).
- Input checks: impossible values (negative counts, zero floor height, 30"
  slab ...) block the calculation; unusual values for a 5-10 marla house are
  flagged for confirmation. Negative quantities can never be produced.
- Results are never stale: Step 4/5 recalculate automatically when inputs
  changed (and say so); unsaved Rooms / Doors & windows edits are kept when
  leaving Step 3 through the sidebar.

**v0.5.1 — readable, interactive export & navigation**
- Excel Summary rewritten for non-technical readers: main materials at a
  glance + a shopping list of every material to buy, grouped by trade
  (collapsible), in purchase units (bags, tons, coils, pipe lengths,
  hundreds of bricks), with when it is needed and how reliable it is.
- Interactive workbook: clickable contents, "◄ Summary" links on every sheet,
  Excel Tables with filter buttons, colour-coded status, click a Mat_ID to jump
  to its calculation, collapsible stage groups, buy quantities as live formulas.
- Only materials inside the selected scope are exported (and shown in Step 4).
- Step 4 has a Shopping list tab; sidebar steps are clickable; every step
  opens scrolled to the top.

**v0.5.0 — drawing-based material take-off**
- The wizard is rebuilt around the Master Material Database: Step 3 reviews
  rooms, doors & windows, counts and structure; Step 4 is the full material
  take-off; Step 5 exports it. Costs removed from the UI for now.
- New label scan reads plumbing labels, the door/chogath schedule and tank
  sizes; rooms/openings edited in Step 3 drive all derived counts.
- Take-off specification in Step 1 (scope packages, finish tier, RCC mix,
  roof system, walling, gas, false ceilings, recharge well, seismic bands).
- Excel export without cost columns; CSV export; structure-aware benchmarks.

**v0.4.0**
- Fixed: footing thickness and founding depth were one field. Footing
  concrete was ~2x too high and excavation was too shallow. They are now
  separate inputs, with backfill, plinth filling and the below-ground column
  stub added.
- Fixed: brick count double-deducted mortar (~30% undercount). Masonry
  laying labour is now priced.
- Fixed: beam concrete double-counted the slab overlap at slab levels.
- Fixed: internal/external plaster split. Ceiling plaster and paint added.
- Added missing scope: staircase, lintels/chajjas, parapet, ground-floor
  soling + PCC base, roof insulation/tiles, parametric MEP.
- Fixed: Step 3 crash on unexpected AI footing types; AI free-text wall
  material no longer overrides the Step 1 choice; malformed AI fields fall
  back per field instead of discarding the whole extraction.
- Fixed: stale drawings analysed after re-upload; drawings kept when
  returning to Step 1.
- PDF text layer now sent to the AI. Steel grade and concrete grade now
  affect quantities/rates; steel grade SI/FPS equivalence corrected
  (Fe415 ≈ Grade 60).
- Input validation before calculation. Sidebar API key and editable
  engineering assumptions (as documented).
- Groq: `qwen/qwen3.8-27b` primary with fallback, 3-image cap, retries.
- BOQ auto-recalculates; exports cached; Excel BOQ uses live formulas.
- Pinned dependencies; deprecated Streamlit `use_container_width` removed.
- Range/benchmark regression tests (`tests/test_accuracy_and_regressions.py`).

**v0.4.0 - tested on a real 5 marla drawing set**
- Refined against an actual 37-page architect's set (renders, site plan,
  plans, elevations, foundation/tank details, doors & windows, plumbing,
  electrical): reads inches written as `''`, rotated (vertical) text, and
  split title/sub-title blocks; picks the best plan variant per floor
  (working details > furniture layout > doors & windows) and ignores MEP
  sheets for geometry.
- **N.T.S sheets:** the scale is calibrated from the drawing's own dimension
  lines (consensus of dimension text vs line length). Wall measurement is
  limited to the plan's dimension ring and the building core, accepts only
  standard brick thicknesses (4½", 9", 13½"), and ignores stair treads,
  hatching and dimension extension lines.
- Reads area-statement tables (and rejects a ground-floor area larger than
  the plot), plot size from the site plan, door schedules (count and average
  size), window sill tags, floor-to-floor height / slab / plinth from
  elevation dimension chains, and detects the marla standard from plot area.
- **Load-bearing houses:** new strip-foundation engine (trench excavation,
  PCC bed, stepped brick footing to plinth with mortar breakdown, backfill),
  detected automatically from wall-section foundation details or chosen in
  Step 3. Isolated-footing results are unchanged.
- Only one page per view goes to the AI (best ground plan, first-floor plan,
  elevation).

**v0.4.0 - 5-10 marla plots & drawing sets**
- Plot templates (5/7/8/10 marla, 225 or 272.25 sqft per marla, 1-3
  storeys) with plausibility checks.
- Free drawing-set analysis: sheet sorting, room/level/schedule/plot text
  parsing and vector wall measurement for CAD PDFs, with cross-sheet
  conflict checks and per-value provenance; only the best pages go to the AI.
- Grouped AI warnings; AI-missing fields fall back to the plot template.
- Tests: `tests/test_package_analysis.py` against a ground-truth sample set.

**v0.4.0 - FPS update**
- FPS (feet-inch, Pakistani practice) is now the default unit system, with
  psi concrete grades, Grade 60 steel, 9" walls, ½"/¾" plaster and round
  feet-inch default dimensions and standard details.
- Complete FPS output: inputs, calculation traces, formulas, descriptions,
  assumptions, validation messages, rate remarks, Step 4/5 screens, Excel
  and PDF. FPS volumes/areas are rounded in cft/sqft.
- SI output is unchanged (verified byte-for-byte against the previous build).
- New `tests/test_fps_system.py`: independent feet-inch hand calculations,
  metric-leak scan of all FPS outputs, and cross-system checks.

## 9. Roadmap ideas (post-MVP)

- Bar Bending Schedule (BBS) generation
- Multi-drawing (plan + elevation + section) cross-referencing
- MEP quantity modules
- Region-specific rate books (state-wise DSR import)
- User accounts + project history (Postgres/Supabase free tier)
- Automatic wall/room polygon detection from CV (contour + Hough lines)
- Confidence calibration using drawing scale detection
