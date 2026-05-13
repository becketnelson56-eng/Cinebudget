# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from `ScriptBreakdown/`.

```bash
# Install dependencies
pip install -r requirements.txt   # anthropic, openpyxl

# Run a full breakdown (requires ANTHROPIC_API_KEY in environment)
python breakdown.py --input script.pdf
python breakdown.py --input script.pdf --output custom-name.xlsx

# Test the Excel writer without the API
python generate_mock_pdf.py       # creates mock_script.pdf
python test_writer.py             # writes test_output.xlsx and verifies formulas/structure
```

## CLI Test Commands

Scripts used for testing during development:

```
python breakdown.py --input john-wick-first-7.pdf
python breakdown.py --input chinatown-first-7.pdf
python breakdown.py --input marriage-story-first-7.pdf
python breakdown.py --input Give-and-Take-Script.pdf
```

---

`test_writer.py` is a legacy V1 test that exercises the old `write_breakdown(items, ...)` interface and imports a `DEPARTMENTS` constant that no longer exists in the current V2 `excel_writer.py`. It will fail against the current code. Use it only as a reference for the formula/structure verification logic; the actual current interface is `write_breakdown(data: dict, output_path, pdf_path)`.

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

## Key Extraction Rules (Summary)

These rules are enforced primarily in `prompts.py` (`SYSTEM_PROMPT`).
The Python enforcer in `breakdown.py` catches structural failures as a safety net.

### Account Routing
- Picture/story vehicles (on-screen) → 2500 Props only, never 3600 Transportation
- Live animals → 3900 Atmosphere (animal) + 3200 Set Operations (handler), never 2500 Props
- Welfare workers, tutors, on-set teachers → 3200 Set Operations only, never 1600 Talent
- Practical steam/smoke/fog → always generate 3000 Mechanical FX row in addition to prop/set dec row
- In-film footage shown on a screen on camera → generate 3100 Special VFX row

### Props vs Set Decoration
- Cast picks it up / holds it / uses it on camera → 2500 Props
- Stays in background, not touched by cast → 2400 Set Decoration
- Same item cannot appear in both accounts — pick one

### Description Field Rules
- One item per row always — never bundle multiple items in one description
- No account category prefix in description (no 'Set decoration —', 'Hero prop —' etc.)
- Hero prop label only for: featured close-up, central to scene conflict, or requires multiples for safety

### Cast & Wardrobe
- Every named speaking cast row in 1600 → matching wardrobe row in 3300 (Python enforcer handles fallback)
- Hand double / body double flagged in any notes field → always generate corresponding 1600 row
- Wardrobe fallback skips crew roles: welfare, guardian, teacher, tutor, coordinator, stand-in, wrangler, handler

### Stunts & Safety
- Any scripted physical contact between cast (even minor) → 2950 Stunt Coordinator flag
- Scripted physical impact against set pieces (wall, furniture, corner) → 2950 safety assessment row
- Custom fabricated photographs depicting animals → flag pre-production animal/handler requirement

### Extras
- Extras only generated when people are affirmatively described as present
- Never generate extras from: empty, eerily empty, deserted, abandoned, silent, vacant, nobody, no one

### Legal Clearance (6200)
- Real trademarks visible on camera → always flag in 6200 (Star Wars, Monopoly, named food brands, etc.)
- Fictional locations, fictional business names, fictional project names → never flag in 6200
- Any song played on camera → flag in 6200 for sync licensing review

### Sound Cues
- Scripted sound cue implying physical on-set event (glass break, crash, impact) → generate row in 2500 Props (breakaway item) and/or 3000 Mechanical FX, Confidence=Medium

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
