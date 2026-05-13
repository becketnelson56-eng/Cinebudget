import base64
import json
import os
import time

import anthropic

from prompts import SYSTEM_PROMPT, account_group_user_message

MODEL = "claude-sonnet-4-6"
MAX_TOKENS_PER_CALL = 8000
# Delay between grouped calls to stay within the 30,000 tokens/minute rate limit.
INTER_CALL_DELAY_SECONDS = 5

# Each tuple: (account_numbers_to_extract, include_scene_breakdown)
# Call 1 also requests the scene breakdown. Remaining calls are account-only.
ACCOUNT_GROUPS: list[tuple[list[str], bool]] = [
    (["1600"], True),                                                        # Cast + scene breakdown
    (["2200", "2300", "2400", "2500"], False),                               # Art dept + props
    (["2600", "2700", "2800", "2900", "2950", "3000", "3100"], False),       # Camera/electric/grip/sound/stunts/FX
    (["3200", "3300", "3400", "3500", "3600", "3900"], False),               # Set ops/wardrobe/makeup/locations/transport/extras
    (["6200"], False),                                                       # Legal clearance
]


def get_breakdown(pdf_bytes: bytes) -> dict:
    """
    Extract all production data from a script PDF.

    Makes 4 API calls across logical account groups, with a 5-second delay
    between calls to respect the rate limit. The first call also extracts the
    Scene Breakdown (one row per scene).

    Returns:
        {
            "scene_breakdown": [...],        # list of scene dicts
            "accounts": {
                "1600": [...],               # list of line-item dicts per account
                "2500": [...],
                ...
            }
        }

    Raises:
        anthropic.APIError: on unrecoverable API failures
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    result: dict = {"scene_breakdown": [], "accounts": {}}
    total = len(ACCOUNT_GROUPS)

    for i, (accounts, include_scenes) in enumerate(ACCOUNT_GROUPS, start=1):
        if i > 1:
            time.sleep(INTER_CALL_DELAY_SECONDS)

        scenes_label = " + scenes" if include_scenes else ""
        acct_label = ", ".join(accounts)
        print(f"  [Call {i}/{total}] {acct_label}{scenes_label}...", end=" ", flush=True)

        try:
            group = _get_group_items(client, pdf_b64, accounts, include_scenes)

            if include_scenes:
                result["scene_breakdown"] = group.get("scene_breakdown", [])

            for acct, items in group.get("accounts", {}).items():
                result["accounts"][acct] = items

            item_count = sum(len(v) for v in group.get("accounts", {}).values())
            if include_scenes:
                scene_count = len(group.get("scene_breakdown", []))
                print(f"{scene_count} scene(s), {item_count} item(s)")
            else:
                print(f"{item_count} item(s)")

        except (json.JSONDecodeError, ValueError) as e:
            print(f"WARNING (skipped): {e}")
        except anthropic.APIError as e:
            print(f"ERROR: {e}")
            raise

    return result


def _get_group_items(
    client: anthropic.Anthropic,
    pdf_b64: str,
    accounts: list[str],
    include_scenes: bool,
) -> dict:
    """
    Extract data for a group of accounts in a single API call.

    Returns dict with 'accounts' (always) and 'scene_breakdown' (when include_scenes).

    Raises:
        json.JSONDecodeError: if the response is not valid JSON
        ValueError: if the response is valid JSON but missing required keys or
                    contains accounts outside the requested set
    """
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_PER_CALL,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": account_group_user_message(accounts, include_scenes),
                    },
                ],
            }
        ],
    )

    raw_text = message.content[0].text.strip()

    # Strip accidental markdown fences if Claude added them despite instructions
    if raw_text.startswith("```"):
        inner = [
            line for line in raw_text.splitlines()
            if not line.startswith("```")
        ]
        raw_text = "\n".join(inner).strip()

    parsed = json.loads(raw_text)

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Expected a JSON object, got {type(parsed).__name__}. "
            f"Raw (first 200 chars): {raw_text[:200]}"
        )

    if "accounts" not in parsed:
        raise ValueError(
            f"Response missing 'accounts' key. "
            f"Raw (first 200 chars): {raw_text[:200]}"
        )

    # Drop any items Claude routed to an account outside this group, warn so it's visible.
    acct_set = set(accounts)
    clean_accounts: dict[str, list] = {}
    for acct, items in parsed["accounts"].items():
        if acct not in acct_set:
            print(
                f"\n    WARNING: unexpected account '{acct}' in response — dropped",
                flush=True,
            )
        elif not isinstance(items, list):
            print(
                f"\n    WARNING: account '{acct}' value is not a list — skipped",
                flush=True,
            )
        else:
            clean_accounts[acct] = items

    result: dict = {"accounts": clean_accounts}
    if include_scenes:
        result["scene_breakdown"] = parsed.get("scene_breakdown", [])

    return result
