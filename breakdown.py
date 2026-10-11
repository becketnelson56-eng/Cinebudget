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
import asyncio
import json
import os
import pathlib
import re
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
            all_gags = []
            scene_nums = []
            for scene in stunt_scenes:
                scene_num = scene.get("scene_number", "?")
                scene_nums.append(str(scene_num))
                all_gags.extend(scene.get("stunts", [])[:3])
            gag_str = "; ".join(all_gags[:6]) or "stunt action"
            scenes_str = " & ".join(scene_nums[:4])
            first_page = stunt_scenes[0].get("script_page")
            coordinator_notes = (
                f"Stunt Coordinator covering all stunt action in production: "
                f"{gag_str} (Scene {scenes_str}). "
                f"Confirm performer-vs-stunt-double requirement for each gag."
            )
            safety_notes = (
                f"Set Safety Officer required on all shoot days with stunt action. "
                f"Scene {scenes_str} is primary stunt day; confirm shoot schedule with AD."
            )
            for desc, notes_text in (
                ("Stunt Coordinator", coordinator_notes),
                ("Safety Officer", safety_notes),
            ):
                acct.append({
                    "account_no": None,
                    "description": f"{desc} — stunt action flagged in scene breakdown",
                    "amount": 1,
                    "unit_type": "Days",
                    "populated_by": "SCRIPT",
                    "script_page": first_page,
                    "script_quote": "",
                    "notes": notes_text,
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
                # Strip the time-of-day suffix (e.g. " - NIGHT") to get the location key
                location = heading.rsplit(" - ", 1)[0].strip() if " - " in heading else heading
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
                        f"Night lighting package upcharge required for {location} "
                        f"(Scene {scene.get('scene_number', '?')}, NIGHT). "
                        f"Confirm with Gaffer on generator requirements and "
                        f"additional electrical crew days for night shoot."
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
        # Strip articles so "The Driver" → ["driver"] and matches "Driver/Kidnapper" etc.
        _NAME_STOP = frozenset({"the", "a", "an", "of", "in", "on", "at", "and"})
        char_words = [w for w in char_lower.split() if w not in _NAME_STOP and len(w) > 1]
        already_covered = any(
            char_lower in r.get("description", "").lower() or
            any(w in r.get("description", "").lower() for w in char_words)
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

    # ── Special Camera → 2600 ─────────────────────────────────────────────────
    _SPECIAL_CAMERA_TRIGGERS = {
        "drone": ("Drone unit + FAA-licensed operator", "Days"),
        "aerial": ("Aerial camera unit", "Days"),
        "underwater": ("Underwater camera housing", "Days"),
        "technocrane": ("Technocrane + remote head operator", "Rental-Day"),
        "techno crane": ("Technocrane + remote head operator", "Rental-Day"),
        "boom": ("Camera crane / jib arm + operator", "Rental-Day"),
        "crane": ("Camera crane + operator", "Rental-Day"),
        "jib": ("Camera jib arm + operator", "Rental-Day"),
        "milo": ("Milo crane arm + remote head operator", "Rental-Day"),
        "condor": ("Condor crane + operator", "Rental-Day"),
        "remote head": ("Remote head camera mount + operator", "Rental-Day"),
        "hot head": ("Hot head remote head mount + operator", "Rental-Day"),
        "helicopter": ("Helicopter/aerial mount + unit", "Days"),
        "high speed": ("High-speed camera rental", "Rental-Day"),
        "slow motion": ("High-speed camera rental — slow motion", "Rental-Day"),
        "slo-mo": ("High-speed camera rental — slow motion", "Rental-Day"),
        "steadicam": ("Steadicam operator + equipment", "Days"),
        "gimbal": ("Camera gimbal rig + operator", "Days"),
        "process trailer": ("Process trailer + camera car rigging", "Rental-Day"),
        "camera car": ("Camera car + rigging", "Rental-Day"),
    }
    all_special_cam = [(s, e) for s in scenes for e in (s.get("special_camera") or []) if e]
    if all_special_cam:
        script_2600 = [r for r in accounts.get("2600", []) if r.get("populated_by") == "SCRIPT"]
        existing_2600_text = " ".join(r.get("description", "").lower() for r in script_2600)
        injected_cam = []
        covered_cam: set[str] = set()
        for scene, entry in all_special_cam:
            entry_lower = entry.lower()
            for kw, (desc, unit) in _SPECIAL_CAMERA_TRIGGERS.items():
                if kw in entry_lower and kw not in covered_cam and kw not in existing_2600_text:
                    covered_cam.add(kw)
                    injected_cam.append({
                        "account_no": None,
                        "description": desc,
                        "amount": 1,
                        "unit_type": unit,
                        "populated_by": "SCRIPT",
                        "script_page": scene.get("script_page"),
                        "script_quote": "",
                        "notes": (
                            f"Auto-generated: scene {scene.get('scene_number', '?')} requires "
                            f"specialist camera equipment ({entry}) — no matching 2600 row found "
                            f"in extraction. Verify equipment type and operator requirements with DP."
                        ),
                        "confidence": "Medium",
                    })
        if injected_cam:
            accounts.setdefault("2600", []).extend(injected_cam)
            print(
                f"  [flow-through] Injected {len(injected_cam)} 2600 specialist camera "
                f"row(s) — scene breakdown flagged specialist equipment."
            )

    # ── Practical FX → 3000 ───────────────────────────────────────────────────
    _PRACTICAL_FX_TRIGGERS = {
        "rain": ("Rain rig — practical precipitation", "Rental-Day"),
        "snow": ("Snow rig — practical snowfall or snow dressing", "Rental-Day"),
        "fog": ("Fog machine + fluid — atmospheric effect, continuity management", "Days"),
        "mist": ("Atmospheric mist rig — continuity management", "Days"),
        "haze": ("Atmospheric haze — diffusion package", "Days"),
        "smoke": ("Smoke machine — on-set practical smoke effect", "Days"),
        "wind": ("Wind machine — on-set practical wind effect", "Rental-Day"),
        "dust": ("Dust rig — practical dust effect", "Allow"),
        "fire": ("Fire rig — practical on-set fire effect", "Allow"),
        "explosion": ("Pyrotechnic rig — practical explosion or blast effect", "Allow"),
        "wave": ("Water/wave rig — practical water effect", "Allow"),
        "flood": ("Water flooding rig — practical water effect", "Allow"),
        "ice": ("Ice/frost set dressing — practical environmental condition", "Allow"),
        "shower": ("Running shower — practical water effect, continuity management", "Days"),
        "water": ("Practical water rig — running water on set", "Days"),
        "tap": ("Practical water rig — running tap/faucet on set", "Days"),
        "faucet": ("Practical water rig — running faucet on set", "Days"),
        "hose": ("Water hose — practical water on set", "Days"),
        "fountain": ("Practical fountain — water effect, continuity management", "Days"),
        "pool": ("Practical water pool/tank — water rigging and drainage", "Rental-Day"),
    }
    # Firearm/armorer items in practical_fx must not trigger 3000 — they route to 2950
    _GUNSHOT_EXCLUSIONS = frozenset({"gunshot", "firearm discharge", "blank fire", "muzzle flash"})
    all_practical_fx = [(s, fx) for s in scenes for fx in (s.get("practical_fx") or []) if fx]
    if all_practical_fx:
        script_3000 = [r for r in accounts.get("3000", []) if r.get("populated_by") == "SCRIPT"]
        existing_3000_text = " ".join(r.get("description", "").lower() for r in script_3000)
        injected_fx = []
        covered_fx: set[str] = set()
        for scene, fx in all_practical_fx:
            fx_lower = fx.lower()
            if any(excl in fx_lower for excl in _GUNSHOT_EXCLUSIONS):
                continue
            for kw, (desc, unit) in _PRACTICAL_FX_TRIGGERS.items():
                if kw in fx_lower and kw not in covered_fx and kw not in existing_3000_text:
                    covered_fx.add(kw)
                    injected_fx.append({
                        "account_no": None,
                        "description": desc,
                        "amount": 1,
                        "unit_type": unit,
                        "populated_by": "SCRIPT",
                        "script_page": scene.get("script_page"),
                        "script_quote": "",
                        "notes": (
                            f"Auto-generated: scene {scene.get('scene_number', '?')} describes "
                            f"a practical on-set effect ({fx}) — no matching 3000 row found in "
                            f"extraction. Verify requirements with Special FX department."
                        ),
                        "confidence": "Medium",
                    })
        if injected_fx:
            accounts.setdefault("3000", []).extend(injected_fx)
            print(
                f"  [flow-through] Injected {len(injected_fx)} 3000 practical FX "
                f"row(s) — scene breakdown flagged weather or on-set practical effects."
            )

    # ── Special Makeup → 3400 ─────────────────────────────────────────────────
    makeup_scenes = [s for s in scenes if s.get("special_makeup")]
    if makeup_scenes:
        script_3400 = [r for r in accounts.get("3400", []) if r.get("populated_by") == "SCRIPT"]
        if not script_3400:
            acct = accounts.setdefault("3400", [])
            for scene in makeup_scenes:
                scene_num = scene.get("scene_number", "?")
                page = scene.get("script_page")
                items = scene.get("special_makeup", [])
                makeup_str = "; ".join(str(m) for m in items[:3])
                acct.append({
                    "account_no": None,
                    "description": f"Special makeup effect — {makeup_str}",
                    "amount": 1,
                    "unit_type": "Days",
                    "populated_by": "SCRIPT",
                    "script_page": page,
                    "script_quote": "",
                    "notes": (
                        f"Auto-generated fallback: scene {scene_num} flagged special makeup "
                        f"({makeup_str}) but extraction produced no 3400 SCRIPT rows."
                    ),
                    "confidence": "Medium",
                })
            print(
                f"  [flow-through] Injected {len(makeup_scenes)} 3400 special makeup "
                f"row(s) — extraction produced none."
            )

    # ── Stunt Driver from 2500 Notes → 2950 ───────────────────────────────────
    _STUNT_DRIVER_PHRASES = ("stunt driver",)
    props_2500 = [r for r in accounts.get("2500", []) if r.get("populated_by") == "SCRIPT"]
    stunt_driver_props = [
        r for r in props_2500
        if any(phrase in (r.get("notes") or "").lower() for phrase in _STUNT_DRIVER_PHRASES)
    ]
    if stunt_driver_props:
        existing_2950_text = " ".join(
            r.get("description", "").lower()
            for r in accounts.get("2950", [])
            if r.get("populated_by") == "SCRIPT"
        )
        if "stunt driver" not in existing_2950_text:
            acct = accounts.setdefault("2950", [])
            for prop_row in stunt_driver_props:
                veh_desc = prop_row.get("description", "picture vehicle")
                acct.append({
                    "account_no": None,
                    "description": f"Stunt driver — {veh_desc}",
                    "amount": 1,
                    "unit_type": "Days",
                    "populated_by": "SCRIPT",
                    "script_page": prop_row.get("script_page"),
                    "script_quote": "",
                    "notes": (
                        f"Auto-generated from picture vehicle row — {veh_desc[:80]}. "
                        f"Stunt operator/driver required for all on-camera vehicle movement. "
                        f"Coordinate with Stunt Coordinator."
                    ),
                    "confidence": "Medium",
                })
            print(
                f"  [flow-through] Injected {len(stunt_driver_props)} 2950 stunt driver "
                f"row(s) — flagged in 2500 Props Notes."
            )

    # ── AHA Compliance → 3200 ────────────────────────────────────────────────
    # Whenever an animal is scripted as injured/harmed/killed, an AHA compliance
    # officer row is required in 3200 (union requirement, separate from wrangler).
    _AHA_INJURY_KEYWORDS = frozenset({"shot", "harmed", "killed", "injured", "wounded"})
    aha_triggered = False
    for row in accounts.get("3900", []):
        notes_lower = (row.get("notes") or "").lower()
        desc_lower = (row.get("description") or "").lower()
        combined = notes_lower + " " + desc_lower
        if any(kw in combined for kw in _AHA_INJURY_KEYWORDS):
            aha_triggered = True
            break
    if not aha_triggered:
        for scene in scenes:
            for animal_entry in (scene.get("animals") or []):
                entry_lower = str(animal_entry).lower()
                if any(kw in entry_lower for kw in _AHA_INJURY_KEYWORDS):
                    aha_triggered = True
                    break
            if aha_triggered:
                break
    if aha_triggered:
        existing_3200_text = " ".join(
            r.get("description", "").lower()
            for r in accounts.get("3200", [])
            if r.get("populated_by") == "SCRIPT"
        )
        if "american humane" not in existing_3200_text and "aha" not in existing_3200_text:
            accounts.setdefault("3200", []).append({
                "account_no": None,
                "description": "American Humane Association compliance officer — animal scripted as injured/killed on camera",
                "amount": 1,
                "unit_type": "Days",
                "populated_by": "SCRIPT",
                "script_page": None,
                "script_quote": "",
                "notes": (
                    "AHA representative required on set for all scenes involving the animal "
                    "in a potentially harmful scenario. Coordinate with wrangler and production attorney."
                ),
                "confidence": "High",
            })
            print(
                "  [flow-through] Injected 3200 AHA compliance officer row — "
                "animal scripted as injured/killed on camera."
            )

    # ── Picture Car Electrical → 2700 ────────────────────────────────────────
    # When a 2500 picture car Notes field references 2700 or mentions a working
    # light bar / electrical rig, auto-inject a 2700 SCRIPT row.
    _PICTURE_CAR_ELECTRICAL_TRIGGERS = ("coordinate with 2700", "light bar", "working electrical")
    for prop_row in accounts.get("2500", []):
        if prop_row.get("populated_by") != "SCRIPT":
            continue
        notes_lower = (prop_row.get("notes") or "").lower()
        if not any(phrase in notes_lower for phrase in _PICTURE_CAR_ELECTRICAL_TRIGGERS):
            continue
        existing_2700_text = " ".join(
            r.get("description", "").lower()
            for r in accounts.get("2700", [])
            if r.get("populated_by") == "SCRIPT"
        )
        if "light bar" in existing_2700_text or "picture car" in existing_2700_text:
            continue
        veh_desc = prop_row.get("description", "picture vehicle")
        if "light bar" in notes_lower:
            elec_desc = (
                "Practical light bar rigging — police/sheriff patrol car picture car, "
                "working siren and light bar required on camera"
            )
            elec_notes = (
                "Coordinate with picture car supplier on whether light bar is functional. "
                "If non-functional, practical electrical rigging required. Confirm with Gaffer."
            )
        else:
            elec_desc = f"Practical electrical rigging — working on-camera electrical for {veh_desc}"
            elec_notes = (
                "Picture car Notes flagged coordination with 2700 for working on-camera electrical. "
                "Confirm rigging requirements with Gaffer and picture car supplier."
            )
        accounts.setdefault("2700", []).append({
            "account_no": None,
            "description": elec_desc,
            "amount": 1,
            "unit_type": "Rental-Day",
            "populated_by": "SCRIPT",
            "script_page": prop_row.get("script_page"),
            "script_quote": "",
            "notes": elec_notes,
            "confidence": "Medium",
        })
        print(
            f"  [flow-through] Injected 2700 electrical row for picture car "
            f"'{veh_desc[:60]}' — Notes flagged electrical/light bar coordination."
        )
        break  # one electrical row per run is sufficient

    # ── Licensed Armorer → 2950 ───────────────────────────────────────────────
    # When any 2500 Props row Notes flag 'armorer required' or 'licensed armorer',
    # verify a 2950 armorer row exists. Generate one if not — SAG-AFTRA requirement.
    _ARMORER_TRIGGERS = ("armorer required", "licensed armorer")
    armorer_props = [
        r for r in accounts.get("2500", [])
        if r.get("populated_by") == "SCRIPT"
        and any(phrase in (r.get("notes") or "").lower() for phrase in _ARMORER_TRIGGERS)
    ]
    if armorer_props:
        existing_2950_text = " ".join(
            r.get("description", "").lower()
            for r in accounts.get("2950", [])
            if r.get("populated_by") == "SCRIPT"
        )
        if "armorer" not in existing_2950_text:
            accounts.setdefault("2950", []).append({
                "account_no": None,
                "description": (
                    "Licensed Armorer — prop firearms on set. "
                    "Required per SAG-AFTRA and production insurance standards."
                ),
                "amount": 1,
                "unit_type": "Days",
                "populated_by": "SCRIPT",
                "script_page": armorer_props[0].get("script_page"),
                "script_quote": "",
                "notes": (
                    "Auto-generated from 2500 firearms row(s) — 'armorer required' flagged in Notes. "
                    "Armorer must be present for all scenes involving prop firearms. "
                    "One row covers entire production — adjust Days to shoot days involving firearms."
                ),
                "confidence": "High",
            })
            print(
                f"  [flow-through] Injected 2950 Licensed Armorer row — "
                f"flagged in {len(armorer_props)} 2500 Props Notes field(s)."
            )

    # ── Welfare Worker (confirmed minor cast only) → 3200 ─────────────────────
    # Only generate when the script explicitly identifies a minor by age, or uses
    # unambiguous child-cast language. "Young [adult character]" flashbacks do NOT qualify.
    _CONFIRMED_MINOR_KW = frozenset({
        "child extras", "child actors", "child cast", "children on set",
        "minor cast", "minor performers", "minors on set", "minor extras",
        "minor background", "child actor", "welfare worker required",
    })
    _AGE_RE = re.compile(r'\b\d+[- ]year[- ]old\b|\bage\s+\d+\b', re.IGNORECASE)

    def _has_confirmed_minor(d: dict) -> bool:
        """Return True only when the script contains an explicitly identified minor."""
        for row in d.get("accounts", {}).get("1600", []):
            if row.get("populated_by") != "SCRIPT":
                continue
            combined = (
                (row.get("description") or "") + " " + (row.get("notes") or "")
            ).lower()
            if _AGE_RE.search(combined) or any(kw in combined for kw in _CONFIRMED_MINOR_KW):
                return True
        for row in d.get("accounts", {}).get("3900", []):
            if row.get("populated_by") != "SCRIPT":
                continue
            desc_lower = (row.get("description") or "").lower()
            if "child" in desc_lower or any(kw in desc_lower for kw in _CONFIRMED_MINOR_KW):
                return True
        return False

    _MINOR_TRIGGERS = (
        "child extras", "child actors", "children on set",
        "minor cast", "minor performers", "minor extras",
        "welfare worker required",
    )
    welfare_trigger_rows = [
        r for r in accounts.get("3900", [])
        if r.get("populated_by") == "SCRIPT"
        and any(phrase in (r.get("notes") or "").lower() for phrase in _MINOR_TRIGGERS)
    ]
    confirmed_minor = _has_confirmed_minor(data)

    # Suppress any existing 3200 welfare worker rows if no confirmed minor found
    if not confirmed_minor:
        original_count = len(accounts.get("3200", []))
        accounts["3200"] = [
            r for r in accounts.get("3200", [])
            if not (
                r.get("populated_by") == "SCRIPT"
                and "welfare" in (r.get("description") or "").lower()
            )
        ]
        suppressed = original_count - len(accounts.get("3200", []))
        if suppressed:
            print(
                f"  [flow-through] Suppressed {suppressed} 3200 Welfare Worker row(s) — "
                f"no confirmed minor identified (welfare worker requires explicit age or "
                f"unambiguous child-cast language)."
            )

    if welfare_trigger_rows and confirmed_minor:
        existing_3200_text = " ".join(
            r.get("description", "").lower()
            for r in accounts.get("3200", [])
            if r.get("populated_by") == "SCRIPT"
        )
        if "welfare" not in existing_3200_text and "studio teacher" not in existing_3200_text:
            trigger = welfare_trigger_rows[0]
            scene_ref = trigger.get("description", "")
            accounts.setdefault("3200", []).append({
                "account_no": None,
                "description": (
                    f"On-set Welfare Worker / Studio Teacher — minor extras present, "
                    f"{scene_ref[:60]}"
                ),
                "amount": 1,
                "unit_type": "Days",
                "populated_by": "SCRIPT",
                "script_page": trigger.get("script_page"),
                "script_quote": "",
                "notes": (
                    "Required when confirmed minor background performers are present. "
                    "Confirm minor headcount with AD — welfare worker-to-minor ratio "
                    "may apply per state labor law."
                ),
                "confidence": "High",
            })
            print(
                "  [flow-through] Injected 3200 Welfare Worker / Studio Teacher row — "
                "confirmed minor extras present in 3900 Background."
            )

    # ── Water Safety → 2950 ──────────────────────────────────────────────────
    # When principal cast are in surf, open water, rivers, pools, or tanks, a
    # dedicated Water Safety Officer row is required — separate from general Safety Officer.
    _WATER_SAFETY_TRIGGERS = frozenset({
        "surf", "breaker", "ocean", "open water", "swim", "wade",
        "underwater", "river", "lake", "tank", "at sea",
    })
    water_cast_scenes = []
    for s in scenes:
        if not s.get("cast"):
            continue
        combined = " ".join([
            " ".join(s.get("practical_fx") or []),
            " ".join(s.get("stunts") or []),
            s.get("wardrobe_notes") or "",
            s.get("heading") or "",
        ]).lower()
        if any(kw in combined for kw in _WATER_SAFETY_TRIGGERS):
            water_cast_scenes.append(s)
    if water_cast_scenes:
        existing_2950_text = " ".join(
            r.get("description", "").lower()
            for r in accounts.get("2950", [])
            if r.get("populated_by") == "SCRIPT"
        )
        if "water safety" not in existing_2950_text:
            accounts.setdefault("2950", []).append({
                "account_no": None,
                "description": "Water Safety Officer — cast in open water, surf, or aquatic sequences",
                "amount": 1,
                "unit_type": "Days",
                "populated_by": "SCRIPT",
                "script_page": water_cast_scenes[0].get("script_page"),
                "script_quote": "",
                "notes": (
                    "Required whenever principal cast are in or adjacent to open water, surf, "
                    "pools, rivers, or tanks. Distinct from general Safety Officer — budget independently. "
                    "Coordinate with Marine Coordinator for vessel and water safety plan."
                ),
                "confidence": "High",
            })
            print(
                f"  [flow-through] Injected 2950 Water Safety Officer row — "
                f"{len(water_cast_scenes)} scene(s) flagged cast in open water or surf."
            )

    # ── Scripted source/live music → 5400 ────────────────────────────────────
    # When any scene has scripted source music (character sings, jukebox, radio on,
    # live band) and no 5400 SCRIPT row exists, inject a placeholder.
    _MUSIC_TRIGGER_KW = frozenset({
        "music plays", "music blares", "jukebox", "radio on", "sings", "singing",
        "plays piano", "plays guitar", "plays violin", "plays drums", "plays the",
        "live music", "live band", "band plays", "orchestra", "dances to",
        "music fills", "song plays", "music from", "tune plays", "melody",
    })
    # Also check 6200 rows for sync-license flags as a secondary indicator
    sync_scenes = []
    for scene in scenes:
        scene_text = " ".join([
            " ".join(str(p) for p in (scene.get("practical_fx") or [])),
            " ".join(str(f) for f in (scene.get("flags") or [])),
            scene.get("wardrobe_notes") or "",
        ]).lower()
        if any(kw in scene_text for kw in _MUSIC_TRIGGER_KW):
            sync_scenes.append(scene)
    # Also check 6200 rows
    has_sync_6200 = any(
        "sync" in (r.get("description") or "").lower()
        or "music" in (r.get("description") or "").lower()
        for r in accounts.get("6200", [])
        if r.get("populated_by") == "SCRIPT"
    )
    if (sync_scenes or has_sync_6200):
        script_5400 = [r for r in accounts.get("5400", []) if r.get("populated_by") == "SCRIPT"]
        if not script_5400:
            source_page = (sync_scenes[0].get("script_page") if sync_scenes else None)
            scene_num = (sync_scenes[0].get("scene_number", "?") if sync_scenes else "?")
            accounts.setdefault("5400", []).append({
                "account_no": None,
                "description": (
                    f"Live/source music — scripted on-set music (Scene {scene_num}) — "
                    f"rights and playback decision pending"
                ),
                "amount": 1,
                "unit_type": "Allow",
                "populated_by": "SCRIPT",
                "script_page": source_page,
                "script_quote": "",
                "notes": (
                    "Scripted source music detected. Three decision paths: "
                    "(1) live musicians on set — budget per session; "
                    "(2) pre-cleared sync + master license for identified track; "
                    "(3) original composition by score composer. "
                    "Named copyrighted music: sync license and master rights clearance IS required. "
                    "Confirm with music supervisor and production legal before shoot day."
                ),
                "confidence": "Medium",
            })
            print(
                f"  [flow-through] Injected 5400 source music row — "
                f"scripted on-set music detected in scene breakdown or 6200 sync flag."
            )

    # ── Period scenes → 2200 production design SCRIPT rows ───────────────────
    # Any scene set >10 years before present or in a named historical era requires
    # a 2200 SCRIPT row for period production design physical work.
    _PERIOD_YEAR_RE = re.compile(r'\b(1[0-9]{3}|20[01][0-9])\b')
    _ERA_KW = frozenset({
        "victorian", "edwardian", "flashback", "1920s", "1930s", "1940s",
        "1950s", "1960s", "1970s", "1980s", "period", "wartime", "wwii",
        "world war", "civil war",
    })
    _CURRENT_DECADE = 2020  # anything before this is considered historical

    period_tags: dict[str, list] = {}  # era_key → list of scene objects
    for scene in scenes:
        heading = scene.get("heading", "").lower()
        wardrobe = (scene.get("wardrobe_notes") or "").lower()
        combined = heading + " " + wardrobe
        # Explicit year in heading (e.g. "INT. DINER - 1971 - DAY")
        years = [int(m) for m in _PERIOD_YEAR_RE.findall(combined) if int(m) < _CURRENT_DECADE]
        if years:
            era_key = f"{min(years)}s"  # use decade as grouping key
            period_tags.setdefault(era_key, []).append(scene)
        # Era keywords
        for kw in _ERA_KW:
            if kw in combined:
                period_tags.setdefault(kw, []).append(scene)
                break  # one tag per scene is enough

    if period_tags:
        script_2200 = [r for r in accounts.get("2200", []) if r.get("populated_by") == "SCRIPT"]
        existing_2200_text = " ".join(r.get("description", "").lower() for r in script_2200)
        for era_key, era_scenes in period_tags.items():
            if era_key in existing_2200_text:
                continue  # already covered
            locations_2200 = list({
                (s.get("heading", "").rsplit(" - ", 1)[0].strip() or s.get("heading", ""))
                for s in era_scenes
            })
            loc_str = "; ".join(locations_2200[:4])
            first_page = era_scenes[0].get("script_page")
            accounts.setdefault("2200", []).append({
                "account_no": None,
                "description": f"Period {era_key.upper()} production design — {loc_str}",
                "amount": 1,
                "unit_type": "Allow",
                "populated_by": "SCRIPT",
                "script_page": first_page,
                "script_quote": "",
                "notes": (
                    f"Period {era_key} production design — physical set work required for "
                    f"{len(era_scenes)} scene(s): {loc_str}. "
                    f"Confirm construction vs dressing-only approach with Production Designer. "
                    f"This is NOT the Art Director DEAL row — it is the physical period work budget."
                ),
                "confidence": "High",
            })
        injected_2200 = len(period_tags)
        if injected_2200:
            print(
                f"  [flow-through] Injected {injected_2200} 2200 period production design "
                f"row(s) — historical period(s) detected in scene headings."
            )

    # ── Suppress unsupported pre-production photography 3200 rows ────────────
    # Claude sometimes generates a pre-production photography/fabrication row in
    # 3200 as a blanket placeholder. Only generate this row when the script
    # explicitly describes a custom fabricated photograph, artwork, or photo
    # double session. If no trigger exists, suppress the row.
    _PREPHOTO_TRIGGER_KW = frozenset({
        "custom photograph", "photo double", "pre-production shoot", "fabricated portrait",
        "fabricated photograph", "prop photograph", "photo session",
    })
    _PREPHOTO_ROW_KW = ("pre-production", "fabrication", "photography session", "fabrication session")
    prephoto_rows = [
        r for r in accounts.get("3200", [])
        if r.get("populated_by") == "SCRIPT"
        and any(kw in (r.get("description") or "").lower() for kw in _PREPHOTO_ROW_KW)
    ]
    if prephoto_rows:
        # Check if any scene breakdown or any account row contains a trigger keyword
        all_text = " ".join(
            " ".join([
                " ".join(str(f) for f in (s.get("flags") or [])),
                " ".join(str(p) for p in (s.get("props") or [])),
                s.get("wardrobe_notes") or "",
            ])
            for s in scenes
        )
        for acct_no, rows in accounts.items():
            for r in rows:
                all_text += " " + (r.get("notes") or "") + " " + (r.get("description") or "")
        all_text_lower = all_text.lower()
        has_trigger = any(kw in all_text_lower for kw in _PREPHOTO_TRIGGER_KW)
        if not has_trigger:
            original_count = len(accounts.get("3200", []))
            accounts["3200"] = [
                r for r in accounts.get("3200", [])
                if not (
                    r.get("populated_by") == "SCRIPT"
                    and any(kw in (r.get("description") or "").lower() for kw in _PREPHOTO_ROW_KW)
                )
            ]
            removed = original_count - len(accounts["3200"])
            if removed:
                print(
                    f"  [flow-through] Suppressed {removed} pre-production photography 3200 "
                    f"row(s) — no script trigger found (no custom photograph, photo double, "
                    f"or fabricated portrait in script)."
                )


# ── Patch 1: suppress self-flagged misrouted rows ─────────────────────────────

_MISROUTED_PHRASES = (
    "route to",
    "moved here in error",
    "reassign to",
    "route primary to",
    "remove from",
    "duplicate flag",
    "do not duplicate",
    "place in 2500",
    "place in 3300",
    "place in 3900",
    "move to 2500",
    "move to 3300",
    "move to 3900",
    "should also have a 2500",
    "should have a 2500",
    "should be in 2500",
    "should be moved to 2500",
    "costume/prop crossover",
)


def _suppress_misrouted_rows(data: dict) -> None:
    """
    Patch 1: Remove SCRIPT rows whose Notes field explicitly flags them as misrouted.

    When Claude places an item in the wrong account and writes a Notes phrase like
    'Route to 2500 — moved here in error', it typically also generates the correct row
    in the target account. This function removes the wrong-account row so only the
    correct row survives. Must run before _enforce_flow_through and before Excel write.
    Modifies data in-place.
    """
    accounts = data.get("accounts", {})
    total = 0
    for acct_no, rows in list(accounts.items()):
        clean = []
        for row in rows:
            notes_lower = (row.get("notes") or "").lower()
            if any(phrase in notes_lower for phrase in _MISROUTED_PHRASES):
                print(
                    f"  [suppress-misrouted] {acct_no}: "
                    f"'{row.get('description', '?')[:70]}' — Notes flagged misrouting, suppressed."
                )
                total += 1
            else:
                clean.append(row)
        accounts[acct_no] = clean
    if total:
        print(f"  [suppress-misrouted] {total} misrouted row(s) removed.")


# ── Patch 3: enforce cross-reference flags → 2500 ────────────────────────────

_CROSS_REF_TRIGGERS = (
    "2500 row is required",
    "dedicated 2500",
    "should also have a 2500",
    "should have a 2500",
    "should be in 2500",
    "should be moved to 2500",
)


def _enforce_cross_reference_flags(data: dict) -> None:
    """
    Patch 3: Auto-inject 2500 SCRIPT rows when Notes or scene breakdown flags
    explicitly call for a dedicated 2500 row but none exists yet.

    Scans:
      - All account rows whose Notes contain '2500 row is required' or 'dedicated 2500'
      - scene_breakdown[].flags containing the same phrases

    Must run after _suppress_misrouted_rows and before _enforce_flow_through.
    Modifies data in-place.
    """
    accounts = data.get("accounts", {})
    scenes = data.get("scene_breakdown", [])
    acct_2500 = accounts.setdefault("2500", [])

    def _existing_norms() -> set:
        return {_norm(r.get("description", "")) for r in acct_2500 if r.get("populated_by") == "SCRIPT"}

    injected = []
    seen_norms: set[str] = set()

    for acct_no, rows in accounts.items():
        for row in rows:
            notes_lower = (row.get("notes") or "").lower()
            if not any(phrase in notes_lower for phrase in _CROSS_REF_TRIGGERS):
                continue
            desc = row.get("description", "")
            n = _norm(desc)
            if not n or n in _existing_norms() or n in seen_norms:
                continue
            seen_norms.add(n)
            # Build a human-readable description — strip any trailing detail after the first dash
            # so the user sees the item name, not internal system trigger language.
            item_name = re.split(r"[—–]|\s\(", desc)[0].strip() or desc.strip()
            injected.append({
                "account_no": None,
                "description": (
                    f"{item_name} — auto-populated from set decoration cross-reference. "
                    f"Verify details with Props Master."
                ),
                "amount": 1,
                "unit_type": "Allow",
                "populated_by": "SCRIPT",
                "script_page": row.get("script_page"),
                "script_quote": row.get("script_quote", ""),
                "notes": (
                    f"Auto-generated from {acct_no} cross-reference flag. "
                    f"Original description: '{desc[:80]}'. "
                    f"Verify routing and item details with Props Master and Set Decorator."
                ),
                "confidence": row.get("confidence", "Medium"),
            })
            print(
                f"  [cross-ref-flags] Injected 2500 row for "
                f"'{desc[:70]}' (flagged in {acct_no} Notes)."
            )

    for scene in scenes:
        for flag in (scene.get("flags") or []):
            flag_lower = flag.lower()
            if not any(phrase in flag_lower for phrase in _CROSS_REF_TRIGGERS):
                continue
            desc = f"Props requirement — {flag}"
            n = _norm(desc)
            if not n or n in _existing_norms() or n in seen_norms:
                continue
            seen_norms.add(n)
            injected.append({
                "account_no": None,
                "description": desc,
                "amount": 1,
                "unit_type": "Allow",
                "populated_by": "SCRIPT",
                "script_page": scene.get("script_page"),
                "script_quote": "",
                "notes": (
                    f"Auto-generated: scene {scene.get('scene_number', '?')} breakdown flag "
                    f"indicated '2500 row is required' or 'dedicated 2500'. "
                    f"Verify with Props department."
                ),
                "confidence": "Medium",
            })
            print(
                f"  [cross-ref-flags] Injected 2500 row for scene "
                f"{scene.get('scene_number', '?')} flag: '{flag[:70]}'."
            )

    if injected:
        acct_2500.extend(injected)
        print(f"  [cross-ref-flags] {len(injected)} 2500 row(s) injected from Notes/flags.")


# ── Patches 2 & 4: cross-account deduplication ────────────────────────────────

_DEDUP_NOISE = frozenset({"hero", "breakaway", "practical", "the", "a", "an", "and"})

# Only treat references to known account numbers as meaningful (avoids matching
# page numbers, years, or other 4-digit values in Notes text).
_KNOWN_ACCOUNTS = frozenset({
    "1600", "2200", "2300", "2400", "2500", "2600", "2700", "2800",
    "2900", "2950", "3000", "3100", "3200", "3300", "3400", "3500",
    "3600", "3900", "6200",
})

_ACCT_REF_RE = re.compile(r"\b([1-9]\d{3})\b")


def _norm(desc: str) -> str:
    """Normalize a description to a stripped word-set for near-match comparison."""
    # Take only the core item name — everything before an em/en dash or opening paren
    core = re.split(r"[—–]|\s\(", desc)[0].lower()
    words = [w.strip(".,;:!?'\"") for w in core.split()]
    return " ".join(w for w in words if w and w not in _DEDUP_NOISE)


def _token_jaccard(a: str, b: str) -> float:
    """Jaccard similarity between word-token sets of two normalized descriptions."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _deduplicate_accounts(data: dict) -> None:
    """
    Patches 2 & 4: Cross-account deduplication with three priority rules.

    Operates only on SCRIPT rows; DEAL/SCHEDULE rows are never touched.
    Rules applied in order (earlier rules take precedence):
      Rule 1 — 2400 vs 2500: keep 2500 (cast-interactive wins over set decoration)
      Rule 2 — 2500 vs 3300: keep 3300 (costume/wardrobe wins over props)
      Rule 3 — Notes-referenced account: if the same item exists in the account
               referenced in a row's Notes field, suppress the source row and keep
               the Notes-referenced account's row.
    Suppressed rows are logged to the terminal.
    Modifies data in-place.
    """
    accounts = data.get("accounts", {})

    def script_norms(acct_no: str) -> set:
        return {
            _norm(r.get("description", ""))
            for r in accounts.get(acct_no, [])
            if r.get("populated_by") == "SCRIPT" and _norm(r.get("description", ""))
        }

    def suppress_from(acct_no: str, target_norms: set, reason: str) -> None:
        rows = accounts.get(acct_no, [])
        clean = []
        for row in rows:
            if row.get("populated_by") != "SCRIPT":
                clean.append(row)
                continue
            n = _norm(row.get("description", ""))
            if n and n in target_norms:
                print(
                    f"  [dedup] Suppressed {acct_no}: "
                    f"'{row.get('description', '?')[:70]}' — {reason}"
                )
            else:
                clean.append(row)
        accounts[acct_no] = clean

    # Rule 1: 2400 vs 2500 — Props wins over Set Decoration (exact + fuzzy match)
    # Fuzzy match catches word-order variants like "RISK board game" vs "RISK game board".
    norms_2500 = script_norms("2500")
    if norms_2500:
        rows_2400 = accounts.get("2400", [])
        clean_2400 = []
        for row in rows_2400:
            if row.get("populated_by") != "SCRIPT":
                clean_2400.append(row)
                continue
            n = _norm(row.get("description", ""))
            if not n:
                clean_2400.append(row)
                continue
            if n in norms_2500 or any(_token_jaccard(n, t) >= 0.4 for t in norms_2500 if t):
                print(
                    f"  [dedup] Suppressed 2400: "
                    f"'{row.get('description', '?')[:70]}' — also in 2500 (Props wins)"
                )
            else:
                clean_2400.append(row)
        accounts["2400"] = clean_2400

    # Rule 2: 2500 vs 3300 — Wardrobe wins over Props (exact + fuzzy match)
    # Catches costume pieces (helmets, uniforms, boots) that appear in both accounts.
    norms_3300 = script_norms("3300")
    if norms_3300:
        rows_2500 = accounts.get("2500", [])
        clean_2500 = []
        for row in rows_2500:
            if row.get("populated_by") != "SCRIPT":
                clean_2500.append(row)
                continue
            n = _norm(row.get("description", ""))
            if not n:
                clean_2500.append(row)
                continue
            if n in norms_3300 or any(_token_jaccard(n, t) >= 0.4 for t in norms_3300 if t):
                print(
                    f"  [dedup] Suppressed 2500: "
                    f"'{row.get('description', '?')[:70]}' — also in 3300 (Wardrobe wins over Props)"
                )
            else:
                clean_2500.append(row)
        accounts["2500"] = clean_2500

    # Rule 2b: Costume keyword scan — any 2500 row describing a wearable item is misrouted
    # to 3300 Wardrobe. 3300 always wins over 2500 for clothing worn by performers.
    _COSTUME_KEYWORDS = frozenset({
        "uniform", "costume", " suit", "dress", "robe", "outfit", "jacket",
        "coat", "shirt", "pants", "blouse", "gown", "attire", "clothing",
        "worn by", "dressed in", "wardrobe",
    })
    rows_2500_cos = accounts.get("2500", [])
    clean_2500_cos = []
    injected_3300_cos = []
    existing_3300_norms = script_norms("3300")
    for row in rows_2500_cos:
        if row.get("populated_by") != "SCRIPT":
            clean_2500_cos.append(row)
            continue
        desc_lower = (row.get("description") or "").lower()
        if any(kw in desc_lower for kw in _COSTUME_KEYWORDS):
            print(
                f"  [dedup] Suppressed 2500: '{row.get('description', '?')[:70]}' — "
                f"wearable costume item (3300 Wardrobe always wins over Props)"
            )
            n = _norm(row.get("description", ""))
            if n and n not in existing_3300_norms:
                injected_3300_cos.append({
                    "account_no": None,
                    "description": row.get("description", ""),
                    "amount": row.get("amount"),
                    "unit_type": row.get("unit_type", "Allow"),
                    "populated_by": "SCRIPT",
                    "script_page": row.get("script_page"),
                    "script_quote": row.get("script_quote", ""),
                    "notes": (
                        (row.get("notes") or "")
                        + " Rerouted from 2500 to 3300 — wearable costume item."
                    ).strip(),
                    "confidence": row.get("confidence", "Medium"),
                })
                existing_3300_norms.add(n)
        else:
            clean_2500_cos.append(row)
    accounts["2500"] = clean_2500_cos
    if injected_3300_cos:
        accounts.setdefault("3300", []).extend(injected_3300_cos)
        print(
            f"  [dedup] Rerouted {len(injected_3300_cos)} 2500 costume row(s) → 3300 Wardrobe."
        )

    # Rule 3: Notes-referenced account — suppress source when same item exists in target
    # Skip pairs already handled by Rules 1 & 2 to avoid double-processing.
    rule12_pairs = {("2400", "2500"), ("2500", "3300")}
    for acct_no in list(accounts.keys()):
        for row in list(accounts.get(acct_no, [])):
            if row.get("populated_by") != "SCRIPT":
                continue
            notes = row.get("notes") or ""
            desc = row.get("description", "")
            n = _norm(desc)
            if not n:
                continue
            for ref_acct in _ACCT_REF_RE.findall(notes):
                if ref_acct not in _KNOWN_ACCOUNTS or ref_acct == acct_no:
                    continue
                if (acct_no, ref_acct) in rule12_pairs:
                    continue
                if n in script_norms(ref_acct):
                    suppress_from(
                        acct_no, {n},
                        f"also in {ref_acct} (Notes-referenced account wins)",
                    )
                    break  # one suppression per row is enough

    # Rule 4: 2500 vs 3000 — Mechanical FX delivery mechanism wins over Props
    # Blood/gore/splatter rig descriptions in both 2500 and 3000 are double-counts.
    # The delivery mechanism (rig, pump, system) belongs in 3000; individual hero
    # material application to a named cast member belongs in 2500/3400.
    _FX_DELIVERY_KW = frozenset({
        "blood", "gore", "flesh", "tissue", "splatter", "gag",
        "blood and gore", "blood shower", "blood jet",
    })
    norms_3000 = script_norms("3000")
    if norms_3000:
        rows_2500_r4 = accounts.get("2500", [])
        clean_2500_r4 = []
        for row in rows_2500_r4:
            if row.get("populated_by") != "SCRIPT":
                clean_2500_r4.append(row)
                continue
            desc_lower = (row.get("description") or "").lower()
            n = _norm(row.get("description", ""))
            is_fx_delivery = any(kw in desc_lower for kw in _FX_DELIVERY_KW)
            if is_fx_delivery and n and any(_token_jaccard(n, t) >= 0.35 for t in norms_3000 if t):
                print(
                    f"  [dedup] Suppressed 2500: "
                    f"'{row.get('description', '?')[:70]}' — "
                    f"practical FX delivery mechanism already in 3000 (Mechanical FX wins)"
                )
            else:
                clean_2500_r4.append(row)
        accounts["2500"] = clean_2500_r4

    # Rule 5: 2700 vs 3000 — Mechanical FX wins for atmospheric fog/smoke/haze
    # Fog machines, haze rigs, and smoke effects placed in 2700 Electric are
    # misrouted — the FX department owns these, not Electric. Suppress 2700 rows
    # describing atmospheric effects when a matching 3000 row already exists.
    _ATMO_FX_KW = frozenset({"fog", "haze", "smoke", "mist", "atmospheric"})
    norms_3000_r5 = script_norms("3000")  # re-evaluate after Rule 4 may have changed 2500
    rows_2700 = accounts.get("2700", [])
    clean_2700 = []
    for row in rows_2700:
        if row.get("populated_by") != "SCRIPT":
            clean_2700.append(row)
            continue
        desc_lower = (row.get("description") or "").lower()
        n = _norm(row.get("description", ""))
        is_atmo = any(kw in desc_lower for kw in _ATMO_FX_KW)
        if is_atmo and norms_3000_r5 and any(_token_jaccard(n, t) >= 0.3 for t in norms_3000_r5 if t):
            print(
                f"  [dedup] Suppressed 2700: "
                f"'{row.get('description', '?')[:70]}' — "
                f"atmospheric FX already in 3000 (Mechanical FX wins; note 2700 power coordination in 3000)"
            )
        else:
            clean_2700.append(row)
    accounts["2700"] = clean_2700


def _consolidate_stunt_rows(data: dict) -> None:
    """
    Fix 8: Merge all 2950 Stunt Coordinator rows into one, and all Safety Officer rows
    into one. Regardless of how many stunt scenes, exactly one Coordinator row and one
    Safety Officer row should exist. Modifies data in-place.
    """
    accounts = data.get("accounts", {})
    script_2950 = [r for r in accounts.get("2950", []) if r.get("populated_by") == "SCRIPT"]

    def _merge_role(role_kw: str) -> None:
        """Merge all SCRIPT rows whose description contains role_kw into one."""
        role_rows = [
            r for r in script_2950
            if role_kw.lower() in (r.get("description") or "").lower()
        ]
        if len(role_rows) <= 1:
            return  # nothing to merge
        # Collect all gag descriptions, stripping the role prefix
        gag_parts = []
        pages = []
        for r in role_rows:
            desc = r.get("description", "")
            # Strip "Stunt Coordinator — " / "Safety Officer — " prefix if present
            for sep in (" — ", " -- ", " - "):
                if sep in desc:
                    desc = desc.split(sep, 1)[1]
                    break
            if desc and desc.lower() not in ("stunt action flagged in scene breakdown",):
                gag_parts.append(desc)
            if r.get("script_page"):
                pages.append(r["script_page"])

        gag_str = "; ".join(dict.fromkeys(gag_parts))[:200] or "all stunt action"
        first_page = pages[0] if pages else None

        if "coordinator" in role_kw.lower():
            merged_desc = f"Stunt Coordinator -- {gag_str}"
            merged_notes = (
                f"Stunt Coordinator covering all stunt action in production: {gag_str}. "
                f"Confirm performer-vs-stunt-double requirement for each gag."
            )
        else:
            merged_desc = f"Safety Officer -- all stunt action"
            merged_notes = (
                f"Set Safety Officer required on all shoot days with stunt action. "
                f"Confirm shoot schedule with AD."
            )

        merged_row = {
            "account_no": None,
            "description": merged_desc,
            "amount": 1,
            "unit_type": "Days",
            "populated_by": "SCRIPT",
            "script_page": first_page,
            "script_quote": "",
            "notes": merged_notes,
            "confidence": "High",
        }

        # Remove all role rows and insert the single merged row
        other_rows = [
            r for r in accounts.get("2950", [])
            if role_kw.lower() not in (r.get("description") or "").lower()
            or r.get("populated_by") != "SCRIPT"
        ]
        accounts["2950"] = other_rows + [merged_row]
        # Rebuild script_2950 so the Safety Officer pass sees the updated list
        script_2950[:] = [
            r for r in accounts["2950"] if r.get("populated_by") == "SCRIPT"
        ]
        print(
            f"  [stunt-consolidate] Merged {len(role_rows)} {role_kw} rows → one row."
        )

    _merge_role("Stunt Coordinator")
    _merge_role("Safety Officer")


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
        data = asyncio.run(get_breakdown(pdf_bytes))
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

    # Post-processing pipeline (order matters — suppress first, then inject, then dedup)
    print("Enforcing cross-reference flags...")
    _enforce_cross_reference_flags(data)

    print("Suppressing self-flagged misrouted rows...")
    _suppress_misrouted_rows(data)

    print("Running structural consistency checks...")
    _enforce_flow_through(data)

    print("Consolidating stunt rows...")
    _consolidate_stunt_rows(data)

    print("Running cross-account deduplication...")
    _deduplicate_accounts(data)

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
