"""
Script Breakdown Tool — Tool 1 of 2

Reads a PDF script and outputs a structured Excel spreadsheet organized by
Movie Magic Budgeting account numbers. All math lives in Excel formulas —
never hardcoded values.

Usage:
    python breakdown.py --input script.pdf
    python breakdown.py --input script.pdf --output custom-name.xlsx
"""

import argparse
import json
import os
import pathlib
import sys

from api_client import get_breakdown
from excel_writer import write_breakdown


def _enforce_flow_through(data: dict) -> None:
    """
    Structural post-processing: enforce consistency rules that Claude sometimes misses.
    Modifies data in-place. Prints a warning for every fallback row injected.

    Rules enforced:
      - Patch 4/7 (JW) / Patch 4 (CT): stunts in scene breakdown → 2950 SCRIPT rows
      - Patch 7 (CT): extras in scene breakdown → 3900 SCRIPT rows
      - Patch 12 (CT): each 1600 SCRIPT cast row → a 3300 wardrobe row (crew roles excluded)
      - Patch 4 (MS): NIGHT scenes → 2700 night lighting rows, one per distinct location
    """
    # Keywords that identify crew roles incorrectly placed in 1600 — skip wardrobe for these.
    _CREW_ROLE_KEYWORDS = {
        "welfare", "guardian", "chaperone", "teacher", "tutor", "coordinator",
        "stand-in", "standin", "wrangler", "handler",
    }
    scenes = data.get("scene_breakdown", [])
    accounts = data.setdefault("accounts", {})

    # ── Stunts → 2950 ─────────────────────────────────────────────────────────
    stunt_scenes = [s for s in scenes if s.get("stunts")]
    if stunt_scenes:
        script_2950 = [r for r in accounts.get("2950", []) if r.get("populated_by") == "SCRIPT"]
        if not script_2950:
            acct = accounts.setdefault("2950", [])
            for scene in stunt_scenes:
                scene_num = scene.get("scene_number", "?")
                page = scene.get("script_page")
                stunt_str = "; ".join(scene.get("stunts", [])[:3])
                note = (
                    f"Auto-generated fallback: scene {scene_num} flagged stunt action(s) "
                    f"({stunt_str}) but extraction produced no 2950 SCRIPT rows."
                )
                for desc in ("Stunt Coordinator", "Safety Officer"):
                    acct.append({
                        "account_no": None,
                        "description": f"{desc} — stunt action flagged in scene breakdown",
                        "amount": 1,
                        "unit_type": "Days",
                        "populated_by": "SCRIPT",
                        "script_page": page,
                        "script_quote": "",
                        "notes": note,
                        "confidence": "Medium",
                    })
            print(
                f"  [flow-through] Injected 2950 fallback rows for "
                f"{len(stunt_scenes)} stunt scene(s) — extraction produced none."
            )

    # ── Extras → 3900 ─────────────────────────────────────────────────────────
    extras_scenes = [
        s for s in scenes
        if (s.get("extras_count") or 0) > 0 or s.get("extras_description")
    ]
    if extras_scenes:
        script_3900 = [r for r in accounts.get("3900", []) if r.get("populated_by") == "SCRIPT"]
        if not script_3900:
            acct = accounts.setdefault("3900", [])
            for scene in extras_scenes:
                scene_num = scene.get("scene_number", "?")
                page = scene.get("script_page")
                count = scene.get("extras_count") or 1
                desc = scene.get("extras_description") or "background performers"
                acct.append({
                    "account_no": None,
                    "description": f"Background extras — {desc}",
                    "amount": count,
                    "unit_type": "Days",
                    "populated_by": "SCRIPT",
                    "script_page": page,
                    "script_quote": "",
                    "notes": (
                        f"Auto-generated fallback: scene {scene_num} flagged {count} extras "
                        f"but extraction produced no 3900 SCRIPT rows."
                    ),
                    "confidence": "Medium",
                })
            print(
                f"  [flow-through] Injected 3900 fallback rows for "
                f"{len(extras_scenes)} extras scene(s) — extraction produced none."
            )

    # ── NIGHT scenes → 2700 ───────────────────────────────────────────────────
    night_scenes = [s for s in scenes if (s.get("day_night") or "").upper() == "NIGHT"]
    if night_scenes:
        script_2700 = [r for r in accounts.get("2700", []) if r.get("populated_by") == "SCRIPT"]
        if not script_2700:
            acct = accounts.setdefault("2700", [])
            seen_locations: set[str] = set()
            for scene in night_scenes:
                heading = scene.get("heading", "")
                # Strip the time-of-day suffix from the slug line to get the location
                location = heading.rsplit(".", 1)[0].strip() if "." in heading else heading
                if location in seen_locations:
                    continue
                seen_locations.add(location)
                acct.append({
                    "account_no": None,
                    "description": f"Night lighting package upcharge — {location}",
                    "amount": 1,
                    "unit_type": "Rental-Day",
                    "populated_by": "SCRIPT",
                    "script_page": scene.get("script_page"),
                    "script_quote": "",
                    "notes": (
                        f"Auto-generated fallback: scene {scene.get('scene_number', '?')} is "
                        f"NIGHT but extraction produced no 2700 SCRIPT rows."
                    ),
                    "confidence": "High",
                })
            print(
                f"  [flow-through] Injected {len(seen_locations)} 2700 night lighting row(s) "
                f"— extraction produced none."
            )

    # ── Cast → Wardrobe ────────────────────────────────────────────────────────
    cast_script = [r for r in accounts.get("1600", []) if r.get("populated_by") == "SCRIPT"]
    wardrobe = accounts.setdefault("3300", [])
    injected = []
    for cast_row in cast_script:
        char_name = cast_row.get("description", "").split("—")[0].split("(")[0].strip()
        if not char_name:
            continue
        # Skip crew roles that were incorrectly placed in 1600 (welfare workers, tutors, etc.)
        if any(kw in char_name.lower() for kw in _CREW_ROLE_KEYWORDS):
            continue
        char_lower = char_name.lower()
        already_covered = any(
            char_lower in r.get("description", "").lower()
            for r in wardrobe + injected
        )
        if not already_covered:
            injected.append({
                "account_no": None,
                "description": f"{char_name} — wardrobe. Coordinate with Costume Designer.",
                "amount": 1,
                "unit_type": "Allow",
                "populated_by": "SCRIPT",
                "script_page": cast_row.get("script_page"),
                "script_quote": "",
                "notes": (
                    "Auto-generated fallback: every named cast member requires a wardrobe row. "
                    "Verify period, costume, and fitting details with Costume Designer."
                ),
                "confidence": "Medium",
            })
    if injected:
        wardrobe.extend(injected)
        print(
            f"  [flow-through] Injected {len(injected)} 3300 wardrobe fallback row(s) "
            f"for unmatched cast member(s)."
        )


def _default_output(input_path: str) -> str:
    """Derive output path from input: 'Whiplash-script.pdf' -> 'Whiplash-breakdown.xlsx'."""
    stem = pathlib.Path(input_path).stem          # 'Whiplash-script'
    # Replace a trailing '-script' or '_script' suffix if present; otherwise just append
    for suffix in ("-script", "_script", "-screenplay", "_screenplay"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return str(pathlib.Path(input_path).parent / f"{stem}-breakdown.xlsx")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract production line items from a script PDF into a budget spreadsheet."
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="SCRIPT.PDF",
        help="Path to the input script PDF.",
    )
    parser.add_argument(
        "--output",
        required=False,
        default=None,
        metavar="BREAKDOWN.XLSX",
        help="Path for the output Excel spreadsheet. "
             "Defaults to <input-stem>-breakdown.xlsx in the same directory.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Resolve output path — use explicit arg or derive from input filename
    output_path = args.output or _default_output(args.input)

    # Validate input file
    if not os.path.isfile(args.input):
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if not args.input.lower().endswith(".pdf"):
        print(f"WARNING: Input file does not have a .pdf extension: {args.input}")

    # Read PDF bytes
    print(f"Reading: {args.input}")
    with open(args.input, "rb") as f:
        pdf_bytes = f.read()

    if len(pdf_bytes) == 0:
        print("ERROR: Input PDF is empty.", file=sys.stderr)
        sys.exit(1)

    # Call Claude API
    print("Sending to Claude API for breakdown extraction...")
    try:
        data = get_breakdown(pdf_bytes)
    except json.JSONDecodeError as e:
        print(f"ERROR: Claude returned invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: Unexpected response from Claude: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: API call failed: {e}", file=sys.stderr)
        sys.exit(1)

    scene_count = len(data.get("scene_breakdown", []))

    # Enforce structural consistency (stunt/extras/wardrobe flow-through)
    print("Running structural consistency checks...")
    _enforce_flow_through(data)

    accounts = data.get("accounts", {})
    total_items = sum(len(v) for v in accounts.values())

    if scene_count == 0 and total_items == 0:
        print("WARNING: Claude returned 0 scenes and 0 line items. Writing empty spreadsheet.")

    print(f"Extracted {scene_count} scene(s) and {total_items} line item(s). Writing spreadsheet...")
    print(f"Output: {output_path}")

    # Write Excel file
    try:
        pdf_path = os.path.abspath(args.input)
        write_breakdown(data, output_path, pdf_path)
    except NotImplementedError as e:
        print(f"\nNOTE: {e}", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: Failed to write spreadsheet: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Done: {scene_count} scenes + {total_items} items written to {output_path}")

    # Print account summary to console
    if accounts:
        print("\nItems by account:")
        for acct_no in sorted(accounts.keys()):
            count = len(accounts[acct_no])
            if count:
                print(f"  {acct_no}: {count}")


if __name__ == "__main__":
    main()
