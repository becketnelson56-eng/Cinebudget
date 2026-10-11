import asyncio
import io
import json
import os

import anthropic
import pdfplumber

from prompts import CORE_RULES, CALL_PROMPTS, account_group_user_message

SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5-20251001"
MAX_TOKENS_PER_CALL = 8000
MAX_RETRIES = 4
BASE_BACKOFF_SECONDS = 2.0

# calls 1,2,4 → Sonnet (cast/art dept/set ops require highest accuracy)
# calls 3,5  → Haiku  (camera/FX/legal are lower-complexity extractions)
CALL_MODELS = [SONNET, SONNET, HAIKU, SONNET, HAIKU]

# Each tuple: (account_numbers_to_extract, include_scene_breakdown)
# Call 1 also requests the scene breakdown. Remaining calls are account-only.
ACCOUNT_GROUPS: list[tuple[list[str], bool]] = [
    (["1600"], True),                                                        # Cast + scene breakdown
    (["2200", "2300", "2400", "2500"], False),                               # Art dept + props
    (["2600", "2700", "2800", "2900", "2950", "3000", "3100"], False),       # Camera/electric/grip/sound/stunts/FX
    (["3200", "3300", "3400", "3500", "3600", "3900", "5400"], False),       # Set ops/wardrobe/makeup/locations/transport/extras/source-music
    (["6200"], False),                                                       # Legal clearance
]


async def get_breakdown(pdf_bytes: bytes) -> dict:
    """
    Extract all production data from a script PDF.

    Fires all 5 API calls in parallel via asyncio.gather. Exponential backoff
    on 429 rate-limit errors. JSON/value parse errors on individual calls are
    skipped with a warning; API errors propagate and fail the job.

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
    client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    script_text = _extract_text(pdf_bytes)
    total = len(ACCOUNT_GROUPS)

    tasks = [
        _get_group_items(
            client, script_text,
            accounts, include_scenes,
            CALL_PROMPTS[i], CALL_MODELS[i],
            call_num=i + 1, total=total,
        )
        for i, (accounts, include_scenes) in enumerate(ACCOUNT_GROUPS)
    ]

    # return_exceptions=True: one call failing doesn't cancel the rest
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    result: dict = {"scene_breakdown": [], "accounts": {}}
    for group, (accounts, include_scenes) in zip(raw_results, ACCOUNT_GROUPS):
        if isinstance(group, anthropic.APIError):
            raise group
        if isinstance(group, (json.JSONDecodeError, ValueError)):
            print(f"WARNING (skipped): {group}")
            continue
        if isinstance(group, Exception):
            raise group
        if include_scenes:
            result["scene_breakdown"] = group.get("scene_breakdown", [])
        for acct, items in group.get("accounts", {}).items():
            result["accounts"][acct] = items

    return result


def _extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [page.extract_text() for page in pdf.pages]
    return "\n\n".join(p for p in pages if p)


async def _get_group_items(
    client: anthropic.AsyncAnthropic,
    script_text: str,
    accounts: list[str],
    include_scenes: bool,
    call_prompt: str,
    model: str,
    call_num: int,
    total: int,
) -> dict:
    """
    Extract data for a group of accounts in a single async API call.

    Retries up to MAX_RETRIES times on 429 with exponential backoff.

    Returns dict with 'accounts' (always) and 'scene_breakdown' (when include_scenes).

    Raises:
        json.JSONDecodeError: if the response is not valid JSON
        ValueError: if the response is valid JSON but missing required keys or
                    contains accounts outside the requested set
        anthropic.APIError: on unrecoverable API failures
    """
    scenes_label = " + scenes" if include_scenes else ""
    acct_label = ", ".join(accounts)
    print(f"  [Call {call_num}/{total}] {acct_label}{scenes_label}...", end=" ", flush=True)

    system_blocks = [
        {"type": "text", "text": CORE_RULES, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": call_prompt},
    ]
    user_text = f"SCREENPLAY TEXT:\n\n{script_text}\n\n{account_group_user_message(accounts, include_scenes)}"

    message = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            message = await client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS_PER_CALL,
                system=system_blocks,
                messages=[{"role": "user", "content": user_text}],
            )
            break
        except anthropic.RateLimitError:
            if attempt >= MAX_RETRIES:
                raise
            wait = BASE_BACKOFF_SECONDS * (2 ** attempt)
            print(
                f"\n  [Call {call_num}] Rate limited — retrying in {wait:.0f}s "
                f"(attempt {attempt + 1}/{MAX_RETRIES})",
                flush=True,
            )
            await asyncio.sleep(wait)

    raw_text = message.content[0].text.strip()

    # Strip accidental markdown fences if Claude added them despite instructions
    if raw_text.startswith("```"):
        inner = [line for line in raw_text.splitlines() if not line.startswith("```")]
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

    item_count = sum(len(v) for v in clean_accounts.values())
    if include_scenes:
        scene_count = len(parsed.get("scene_breakdown", []))
        print(f"{scene_count} scene(s), {item_count} item(s)")
    else:
        print(f"{item_count} item(s)")

    result: dict = {"accounts": clean_accounts}
    if include_scenes:
        result["scene_breakdown"] = parsed.get("scene_breakdown", [])
    return result
