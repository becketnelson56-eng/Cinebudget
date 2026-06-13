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
                            f"Auto-generated: scene {scene.get('scene_number', '?')} flagged "
                            f"'{entry}' in special_camera — no matching 2600 row found."
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
                            f"Auto-generated: scene {scene.get('scene_number', '?')} flagged "
                            f"'{fx}' in practical_fx — no matching 3000 row found."
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
                        f"Auto-generated: 2500 Props row for '{veh_desc[:70]}' Notes "
                        f"flagged 'stunt driver' — auto-injected 2950 row."
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

    # ── Welfare Worker (minor extras) → 3200 ─────────────────────────────────
    # When any 3900 Background row Notes flag minors/children, verify a 3200
    # welfare worker row exists. Required when minors are on set.
    _MINOR_TRIGGERS = ("minor", "child extras", "welfare worker", "children required")
    welfare_trigger_rows = [
        r for r in accounts.get("3900", [])
        if r.get("populated_by") == "SCRIPT"
        and any(phrase in (r.get("notes") or "").lower() for phrase in _MINOR_TRIGGERS)
    ]
    if welfare_trigger_rows:
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
                    "Auto-generated: 3900 Background row Notes flagged minor extras. "
                    "Required when minor background performers are present. "
                    "Confirm minor headcount with AD — welfare worker-to-minor ratio may apply per state labor law."
                ),
                "confidence": "High",
            })
            print(
                "  [flow-through] Injected 3200 Welfare Worker / Studio Teacher row — "
                "minor extras flagged in 3900 Background Notes."
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

    # Rule 2: 2500 vs 3300 — Wardrobe wins over Props
    norms_3300 = script_norms("3300")
    if norms_3300:
        suppress_from("2500", norms_3300, "also in 3300 — Wardrobe wins over Props")

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

    # Post-processing pipeline (order matters — suppress first, then inject, then dedup)
    print("Enforcing cross-reference flags...")
    _enforce_cross_reference_flags(data)

    print("Suppressing self-flagged misrouted rows...")
    _suppress_misrouted_rows(data)

    print("Running structural consistency checks...")
    _enforce_flow_through(data)

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
