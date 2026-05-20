# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the project root.

```bash
# Install dependencies
pip install -r requirements.txt   # anthropic, openpyxl

# Run a full breakdown (requires ANTHROPIC_API_KEY in environment)
python breakdown.py --input script.pdf
python breakdown.py --input script.pdf --output custom-name.xlsx

```

## CLI Test Commands

Scripts used for testing during development:

```
python breakdown.py --input john-wick-first-7.pdf
python breakdown.py --input chinatown-first-7.pdf
python breakdown.py --input marriage-story-first-7.pdf
python breakdown.py --input Give-and-Take-Script.pdf
```

## Test Run History

Scripts tested and patch rounds completed:

1. Give-and-Take-Script.pdf — initial V2 schema test
2. john-wick-first-7.pdf — Run 1 (major routing fixes)
3. chinatown-first-7.pdf — Run 1 (description format, bundling, 2400/2500 split)
4. marriage-story-first-7.pdf — Run 1 (lean drama test, welfare worker, night lighting)
5. hereditary-first-7.pdf — Run 1 (miniature VFX, featured background, account numbering)
6. get-out-first-7.pdf — Run 1 (wardrobe fallback dedup, interior locations, Andre count)
7. game-night-first-7.pdf — Run 1 (montage locations, source music, non-applicable rows)
8. john-wick-first-7.pdf — Run 2 (deduplication, cross-reference enforcement, desk row)

Current status: all patch docs submitted. Tool is integrated into Flask web UI.
Next: AWS deployment, then Handsontable live spreadsheet in Budget tab.

---

## Architecture

The tool is a 5-file pipeline:

```
breakdown.py  →  api_client.py  →  prompts.py
     ↓                ↓
_enforce_flow_through()    returns dict: {scene_breakdown, accounts}
     ↓
excel_writer.py  →  .xlsx output
```

**`breakdown.py`** — CLI entry point. Reads the PDF, calls the API, runs the post-processing enforcer (`_enforce_flow_through`), then calls the Excel writer.

**`api_client.py`** — Makes **5 grouped API calls** to Claude, each passing the full PDF as a base64 `document` block alongside a user-turn prompt. Call 1 also extracts the Scene Breakdown. Groups:
1. `["1600"]` + scene breakdown
2. `["2200", "2300", "2400", "2500"]`
3. `["2600", "2700", "2800", "2900", "2950", "3000", "3100"]`
4. `["3200", "3300", "3400", "3500", "3600", "3900"]`
5. `["6200"]`

Results are merged into a single `{"scene_breakdown": [...], "accounts": {"1600": [...], ...}}` dict. A 5-second delay between calls respects the rate limit.

**`prompts.py`** — Two exports: `SYSTEM_PROMPT` (a long constant with all extraction rules, routing logic, and industry-specific guidance) and `account_group_user_message()` (builds the per-call user turn specifying which account numbers to extract). The system prompt is the primary enforcement layer for all production rules.

**`breakdown.py._enforce_flow_through(data)`** — Post-processing safety net that runs *after* the API returns, *before* Excel is written. Injects fallback rows when Claude misses required structural links:
- NIGHT scenes in scene_breakdown → 2700 night lighting rows (one per distinct location)
- Non-empty `stunts` array → 2950 Stunt Coordinator + Safety Officer rows
- Non-empty `extras_count`/`extras_description` → 3900 Background extras rows
- Each 1600 SCRIPT cast row → a 3300 wardrobe row (skips crew-role descriptions: welfare, guardian, teacher, tutor, coordinator, stand-in, wrangler, handler)

**`excel_writer.py`** — Builds the workbook. Key structures:
- `ACCOUNTS`: 35 `(account_no, account_name)` tuples defining every tab in order
- `ACCOUNT_TEMPLATES`: pre-populated DEAL/SCHEDULE placeholder rows per account
- Tab naming: `f"{acct_no} – {acct_name}"` using en dash (U+2013)
- **Universal column layout A–O** on every account tab: A=Account No, B=Description, C=Amount, D=Unit Type, E=Rate (blank), F=`=IFERROR(C*D*E,"")`, G=Fringe% (blank), H=`=IFERROR(F+(F*G),"")`, I=Vendor (blank), J=Vendor URL (blank), K=Script Page (hyperlink), L=Script Quote, M=Notes, N=Confidence, O=Populated By
- **Top Sheet**: SUM formulas referencing each account tab's H column; formula strings like `SUM('2500 – Props'!H:H)`. Tab names containing spaces or `&` must be wrapped in single quotes in formula references. `/` is invalid in Excel tab names — use `&` (see 5200, 5600 in ACCOUNTS).
- Column K hyperlinks: `file:///` URI pointing to the input PDF; links to specific pages.
- Color coding (column O): SCRIPT=green, SCHEDULE=yellow, DEAL=pink, FORMULA=blue.
- **All math is formula strings** — never hardcode a calculated value. Formula cells contain strings starting with `=`.

## Web Application (Flask UI)

The breakdown tool is wrapped in a Flask web application (`app.py`) that provides a browser-based interface for the tool.

### Running the web app

```
$env:ANTHROPIC_API_KEY='your_key_here'  # PowerShell — must reload each session
python app.py
# Then open http://localhost:5000 in your browser
```

### Login credentials

Hardcoded in `app.py` for development purposes. Ask Claude Code for current credentials.

### Tab structure

- Budget (placeholder — future live Handsontable spreadsheet)
- Script Breakdown (active — PDF upload → .xlsx download)
- Scheduling (placeholder — future 1st AD input)
- Deals (placeholder — future contract upload)
- Export (placeholder — future multi-format export)

### Flask route logic

The Script Breakdown tab: uploads PDF to temp folder → calls breakdown.py pipeline directly (imported, not subprocess) → returns .xlsx as file download.
Error handling: API failures display error message in UI rather than crashing.

### GitHub

Repo: https://github.com/becketnelson56-eng/Cinebudget
Push after significant changes: `git add . && git commit -m 'message' && git push`

## Key Extraction Rules (Summary)

Rules enforced in `prompts.py` (`SYSTEM_PROMPT`). Python enforcer catches structural failures.

### Account Routing
- Picture/story vehicles (on-screen) → 2500 Props only, never 3600 Transportation
- Live animals → 3900 Atmosphere (animal) + 3200 Set Operations (handler), never 2500
- Welfare workers, tutors, on-set teachers → 3200 only, never 1600 Talent
- Practical steam/smoke/fog → always generate 3000 Mechanical FX row in addition to prop/set dec row
- In-film footage shown on a screen on camera → generate 3100 Special VFX row
- Running water on camera (shower, tap, fountain) → generate 3000 Mechanical FX row
- Scripted crane/boom/jib/drone camera move → generate 2600 Camera equipment row
- Moving vehicle interior photography → generate 2600 process trailer / camera car row
- Scripted sound cue implying physical on-set event → generate 2500 or 3000 row
- Non-applicable placeholder rows (no child cast, etc.) → DO NOT generate at all

### Props vs Set Decoration
- Cast picks it up / holds it / uses it on camera → 2500 Props
- Stays in background, not touched by cast → 2400 Set Decoration
- Same item cannot appear in both accounts — if 2500 row exists, suppress 2400 row
- Any container opened on camera → flag interior contents as set dec or prop
- Set piece with 3+ scripted cast actions → dedicated 2500 row required

### Description Field Rules
- One item per row always — never bundle multiple items in one description
- No account category prefix (no 'Set decoration —', 'Hero prop —' etc.)
- Hero prop label only for: featured close-up, central to scene conflict, multiples needed
- Never flag fictional locations, names, or businesses in 6200 Legal

### Cast, Wardrobe & Makeup
- Every named speaking cast row in 1600 → matching 3300 wardrobe row (Python enforcer)
- Wardrobe fallback skips crew roles: welfare, guardian, teacher, tutor, coordinator, stand-in, wrangler, handler
- Wardrobe fallback skips cast members already covered by a detailed 3300 SCRIPT row (check character name contains() match before generating fallback)
- Hand double / body double flagged in notes → generate corresponding 1600 row
- Actively handled costume pieces → note cross-department in 3300 Notes, do NOT move to 2500
- Scripted wounds, blood, sweat, tears → generate 3400 Makeup row
- Hero costume minimum quantity = 2 units always

### Stunts & Safety
- Any scripted physical contact between cast → 2950 Stunt Coordinator flag
- Scripted physical impact against set pieces → 2950 safety assessment row
- Two+ principal cast in one stunt → stunt performer standby row required
- Picture vehicle with scripted driving action → 2950 stunt driver row required
  (PYTHON-LAYER: if 2500 picture vehicle Notes contain 'stunt driver', auto-generate 2950 row)

### Extras
- Extras only from affirmative presence descriptions — never from empty/deserted/silent
- Featured background with scripted individual action → 1600 day player row, not just 3900
- Named background characters physically present → 1600 row; no individual action → may be 3900

### Locations (3500)
- Every distinct INT. or EXT. location heading → 3500 row, including montage locations
- Practical interior locations (apartments, offices) → 3500 facility rental row
- Scene count brevity does not reduce location fee

### Legal Clearance (6200)
- Real trademarks visible on camera → always flag in 6200 (games, food, clothing, tech brands)
- Fictional locations, business names, project names → never flag in 6200
- Any music playing on camera (source music, radio, jukebox, speakers) → 6200 sync flag
- Named songs playing through a device on camera → 6200 sync flag (both master + sync rights)
- On-camera screen content (phone, TV, computer) → flag for clearance or original content
- GIFs or viral content shown on camera → 6200 clearance flag

### Night Lighting (Python enforcer)
- NIGHT slug → 2700 night lighting row, one per distinct location/setup
- Interior night and exterior night are different setups — separate rows

### Flow-Through Enforcement Summary (Python Layer)
- Stunt flow: non-empty Stunts field → 2950 Coordinator + Safety Officer rows
- Night lighting: NIGHT scene → 2700 row per distinct location
- Extras flow: non-empty extras field → 3900 row
- Wardrobe fallback: each 1600 SCRIPT row → 3300 row (with filters above)
- Special camera flow: non-empty Special Camera field → 2600 equipment row
- Special makeup flow: non-empty Special Makeup field → 3400 row
- Picture vehicle stunt driver: 2500 Notes containing 'stunt driver' → 2950 row
- Cross-ref enforcement: Notes containing '2500 row required' → auto-generate 2500 row
- Notes cross-referencing another account → verify that account has the corresponding row

---

## Key invariants

- `_enforce_flow_through` modifies `data` in-place before `write_breakdown` is called. Fallback rows set `account_no: None` so the writer auto-generates sequential sub-numbers.
- The system prompt is the primary rule layer; the Python enforcer is the safety net. When the same rule exists in both, the prompt defines the ideal and Python catches failures.
- Two patches from the same test run can interact: if a crew role (welfare worker) is incorrectly placed in 1600 by Claude, the wardrobe fallback will also fire for it. The crew-keyword filter in `_enforce_flow_through` prevents this secondary noise.

---

## Patch History Summary

The following issues have been identified during test runs and fixed.
Do not reintroduce these behaviours.

### Routing Fixes
- Animals/handlers removed from 2500 — now correctly placed in 3900/3200
- Picture vehicles removed from 3600 — now correctly placed in 2500
- Welfare worker removed from 1600 — crew members never placed in Talent account

### Extraction Quality Fixes
- Hero prop label overuse fixed — restricted to featured/central/safety-critical items only
- Bundled multi-item rows fixed — one item per row enforced
- Account category prefixes removed from description field
- 2400/2500 double-counting fixed — cast-interaction rule enforced
- Extras no longer generated from negative space/empty scene descriptions
- Fictional names/locations no longer flagged in 6200 Legal
- Trademark clearance applied consistently to all real brands on camera
- Breakaway/destructible set pieces now generate dedicated 2300/2500 rows
- Practical steam effects now generate 3000 Mechanical FX rows in addition to prop rows
- Sound cues implying physical events now generate account rows
- In-film footage shown on screen now generates 3100 row
- Minor scripted physical impacts now generate 2950 safety flag
- Hand double flags in notes now generate corresponding 1600 rows
- Location rental rows default to Amount=1 when shoot days unknown
- Hero costume quantities default to minimum 2 units
- Pre-production photo shoot animal requirements now flagged
- Shoes and similar incidentally-handled items correctly placed in 2500 not 2400

### Flow-Through Enforcement (Python Layer)
- Stunt flow-through: 2950 rows auto-injected when scene breakdown Stunts column is non-empty
- Night lighting flow-through: 2700 rows auto-injected for NIGHT scenes
- Extras flow-through: 3900 rows auto-injected when scene breakdown extras column is non-empty
- Wardrobe fallback: 3300 rows auto-injected for each 1600 cast row (crew-role filter prevents false positives)

### JW Run 2 Patches (Python Layer + Prompt)
- **Patch 1** — Self-flagged misrouted rows suppressed: `_suppress_misrouted_rows()` scans all SCRIPT rows for Notes containing 'route to', 'moved here in error', 'reassign to', 'route primary to' and removes those rows before Excel write. The correct-account row (already present in JSON) is the only one kept.
- **Patch 2** — Costume deduplication: `_deduplicate_accounts()` Rule 2 removes any 2500 row whose normalized description matches a 3300 row (Wardrobe wins over Props). Prompt updated: costume items in 3300 ONLY, never create a 2500 row for a worn costume regardless of on-camera handling.
- **Patch 3** — Cross-account references must generate rows: prompt rule added — any Notes field referencing another account ('see 3000', 'flag for 6200') requires a complete row in that account. A cross-reference note is not a substitute for the row.
- **Patch 4** — General deduplication pass: `_deduplicate_accounts()` runs three priority rules after `_enforce_flow_through`: (1) 2400 vs 2500 → keep 2500, (2) 2500 vs 3300 → keep 3300, (3) Notes-referenced account has same item → suppress source. Suppressed rows logged to terminal.
- **Patch 5** — Multi-action set pieces require dedicated 2500 row: prompt rule added — any set piece used as primary surface/object for 3+ scripted cast actions gets its own 2500 row independent of any 2400 entry. Lookup table entry added.

### Hereditary Patches (Python Layer + Prompt)
- **Patch 1** — Account number sub-index collision: `_write_account_rows` in `excel_writer.py` now tracks `highest_used` sub-index across all SCRIPT rows (including explicit `account_no` fields) and starts DEAL/SCHEDULE template rows at `max(sub_idx, highest_used + 1)` to prevent index collisions.
- **Patch 2** — Broadened misrouted-row suppression: `_MISROUTED_PHRASES` extended with `'remove from'`, `'duplicate flag'`, `'do not duplicate'`, `'place in 2500'`, `'place in 3300'`, `'place in 3900'`. These phrases in a SCRIPT row's Notes trigger removal identical to the JW Run 2 phrases.
- **Patch 3** — Cross-reference flag enforcement: new `_enforce_cross_reference_flags(data)` function auto-injects a 2500 SCRIPT placeholder row whenever any account row's Notes says `'2500 row is required'` or `'dedicated 2500'` and no matching 2500 row already exists. Also scans `scene_breakdown[].flags`. Runs after `_suppress_misrouted_rows`, before `_enforce_flow_through`. Imported and called from both `breakdown.py` and `app.py`.
- **Patch 4** — Featured extras → 1600 day player: prompt `EXTRAS AND BACKGROUND PERFORMERS` section strengthened — background characters with individually directed scripted actions require BOTH a 3900 row AND a 1600 day-player row. New lookup table entry added.
- **Patch 5** — Specialist camera equipment: new `SPECIALIST CAMERA EQUIPMENT (account 2600)` prompt section; consistency rule requiring a 2600 row for any non-empty `special_camera` array. Python enforcer in `_enforce_flow_through` auto-injects 2600 rows for drone/aerial/underwater/technocrane/steadicam/gimbal etc. when no matching 2600 SCRIPT row exists. New lookup table entry added.
- **Patch 6** — Weather/environmental practical FX: new `WEATHER AND ENVIRONMENTAL PRACTICAL FX (account 3000)` prompt section routing rain/snow/fog/smoke/wind to 3000 Mechanical FX. Python enforcer in `_enforce_flow_through` auto-injects 3000 rows for weather keywords in `practical_fx` array when no matching 3000 SCRIPT row exists. New lookup table entry added.
- **Patch 7** — Moving vehicle interior: new `MOVING VEHICLE INTERIOR PHOTOGRAPHY (account 2600)` prompt section — any `INT. [VEHICLE] - MOVING` slug requires a 2600 Process Trailer / Camera Car row. Applies to all moving vehicle interiors (not just action). New lookup table entry added.
- **Patch 8** — Visual impossibility → 3100: new `VISUAL IMPOSSIBILITY AND REALITY TRANSITIONS (account 3100)` prompt section — any visually impossible or physics-defying scene description (size changes, phase-through, dream logic, reality transitions) requires a 3100 row regardless of whether the word 'VFX' appears. New lookup table entry added.

### Get Out Patches (Python Layer + Prompt)
- **Patch 1** — Wardrobe fallback deduplication: `_enforce_flow_through` cast→wardrobe section now uses word-level partial matching to detect already-covered characters. Articles ("the", "a", "an") are stripped before comparison so "The Driver" matches "Driver/Kidnapper" and similar variations. Prevents redundant fallback rows when a detailed 3300 SCRIPT row already exists.
- **Patch 2** — Special makeup → 3400 flow-through: `_enforce_flow_through` now checks `scene_breakdown[].special_makeup` array. If non-empty and no 3400 SCRIPT rows exist, auto-injects placeholder rows. Also added to prompt: scripted wounds, blood, cuts, bruises, injury makeup → 3400. Lookup table entry added.
- **Patch 3** — 2700 night scene location key bug fixed: was using `rsplit(".", 1)[0]` which reduced all slugs to just `"EXT"` or `"INT"`, grouping all night exteriors into one row. Changed to `rsplit(" - ", 1)[0]` to correctly strip only the time-of-day suffix, so `"EXT. SUBURBAN STREET - NIGHT"` → `"EXT. SUBURBAN STREET"` and `"INT. SPORTS CAR - NIGHT"` → `"INT. SPORTS CAR"` are tracked as separate locations.
- **Patch 4** — Practical water sources → 3000: added shower, water, tap, faucet, hose, fountain, pool to `_PRACTICAL_FX_TRIGGERS` in `_enforce_flow_through`. Added `PRACTICAL WATER ON SET` subsection to weather FX prompt section. Lookup table entry added.
- **Patch 5** — Stunt driver cross-reference enforcement: `_enforce_flow_through` now scans all 2500 SCRIPT rows' Notes for "stunt driver" and auto-injects a 2950 Stunt Driver row if none exists. Lookup table entry added.
- **Patch 6** — Interior practical location → 3500: added INT. practical location rule to prompt EXTRACTION RULES section. Any rented real-world interior (apartment, office, restaurant, bar, hospital, school, elevator, hallway) requires a 3500 row. Exception for studio stage/standing sets. Lookup table entry added.
- **Patch 7** — Cast scene count accuracy: strengthened OUTPUT RULES cast Amount rule — count only scenes where character is explicitly present or has action/dialogue. Do NOT count O.S. mentions, dialogue references to the character by others, or implied presence. Lookup table entry added.

### Game Night Patches (Python Layer + Prompt)
- **Patch 1** — No "not applicable" placeholder rows: added explicit OUTPUT RULES rule — never generate placeholder rows for conditions that do not apply. A row saying "welfare worker — not applicable, no child cast identified" is noise and must not be generated. Lookup table entry added.
- **Patch 2** — Source/ambient music → 6200: strengthened MUSIC SYNC LICENSING rule to include ambient source music ('music blares', 'song plays', 'radio on') even when no specific track is named. New `SOURCE / AMBIENT MUSIC` sub-rule added. Lookup table entry added.
- **Patch 3** — Multi-cast stunt gags → stunt performer standby: added `MULTI-CAST STUNT GAGS` rule to stunt section — any gag involving 2+ principal cast simultaneously requires Stunt Coordinator + Safety Officer + Stunt Performer standby, all three. Lookup table entry added.
- **Patch 4** — Montage/brief-scene locations → 3500: strengthened LOCATION ROWS rule — every distinct slug line location requires a 3500 row regardless of scene brevity. Montage and single-shot pickups explicitly called out. Exception for studio stage/standing sets preserved. Lookup table entry added.
- **Patch 5** — 2400/2500 dedup: (a) Added `"move to 2500"`, `"move to 3300"`, `"move to 3900"` to `_MISROUTED_PHRASES` so conditional self-flagging notes ('if cast handles board, move to 2500') trigger row suppression. (b) Added `_token_jaccard` helper and updated Rule 1 in `_deduplicate_accounts` to use fuzzy Jaccard token-overlap (threshold 0.4) in addition to exact norm matching — catches word-order variants like "RISK board game" vs "RISK game board".
- **Patch 6** — Sequential account numbering (gap-free): `_write_script_row` in `excel_writer.py` now always writes the auto-incrementing `sub_idx` to column A, ignoring Claude's original `account_no`. `_write_account_rows` simplified — removed `highest_used` tracking. Template rows follow directly after SCRIPT rows via the same counter, guaranteeing gap-free sequential numbering even when post-processing removes rows. Supersedes Hereditary Patch 1.

### Additional fixes from extended test runs (John Wick Run 2, Chinatown, Marriage Story, Hereditary, Get Out, Game Night)

#### Routing & Account Fixes
- Items self-flagged as wrong account still generating duplicate rows — suppression logic added for Notes containing 'route to', 'remove from', 'moved here in error'
- Costume items no longer duplicated across 2500 and 3300 — 3300 always wins
- Workshop/cast-interactive desks now generate dedicated 2500 rows
- Montage locations now generate 3500 rows — brevity does not exempt them
- Non-applicable placeholder rows removed — no child cast = no welfare worker row

#### Extraction Quality Fixes
- Sound cues implying physical events now generate account rows
- Running water (shower, tap) now generates 3000 Mechanical FX row
- Scripted crane/boom/jib moves now generate 2600 Camera equipment row
- Moving vehicle interiors now generate 2600 process trailer row
- Scripted wounds/blood/sweat now generate 3400 Makeup row
- Source music playing on camera now flagged in 6200
- GIFs and on-screen phone content now flagged for clearance in 6200
- Featured background characters with scripted actions → 1600 day player rows
- Snow/weather coverage now generates 3000 Mechanical FX row
- In-film footage shown on screen now generates 3100 row
- Board games and brand-named props on camera consistently flagged in 6200
- Cast scene counts corrected — O.S. dialogue appearances do not count as scenes

#### Python Layer Additions
- Account number collision fix — no two rows in same tab may share account number
- Account number gap fix — sequential numbering enforced, gaps not permitted
- Wardrobe fallback deduplication — skips cast already covered by detailed 3300 SCRIPT row
- Special camera flow-through — non-empty Special Camera field triggers 2600 row check
- Special makeup flow-through — non-empty Special Makeup field triggers 3400 row check
- Picture vehicle stunt driver flow-through — 2500 Notes trigger 2950 row generation
- Cross-reference enforcement — Notes referencing another account trigger row generation
- 2400/2500 deduplication — if 2500 SCRIPT row exists, suppress matching 2400 row
