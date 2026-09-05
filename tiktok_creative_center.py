"""
tiktok_creative_center.py
-------------------------
Fetches popular hashtags from TikTok Creative Center.

CONFIRMED:
  - URL: https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en
    ?countryCode=<region>&period=7
  - No TikTok ads account is required to browse this page.
  - The page renders server-side + client-side hydration (likely Next.js).

NOT CONFIRMED:
  - The exact shape of the embedded JSON data (if any).
  - Whether __NEXT_DATA__ or a similar script tag contains hashtag data.
  - Whether the page structure will remain stable.

Strategy:
  1. Try loading the page with playwright (headless Chromium).
  2. Look for a __NEXT_DATA__ script tag containing hashtag JSON.
  3. Fall back to a recursive search for dicts with hashtag_name/hashtag_id keys.
  4. If all extraction fails, emit clearly-labeled MOCK data so the rest
     of the pipeline remains runnable.

Both live and mock paths normalize to the shared schema (entity_type="hashtag").

Requires: playwright (pip install playwright && playwright install chromium)
  If playwright is not available, falls back to mock data automatically.

Output: snapshot_<timestamp>.json in the shared schema.

Usage:
    python tiktok_creative_center.py
    python tiktok_creative_center.py --geo PK
    python tiktok_creative_center.py --mock    # force mock data
"""

import json
import argparse
from datetime import datetime, timezone
from snapshot_schema import make_entity, snapshot_filename, write_snapshot


DEFAULT_GEO = "PK"
TIKTOK_CC_URL = (
    "https://ads.tiktok.com/business/creativecenter"
    "/inspiration/popular/hashtag/pc/en"
)


# -- Mock data for when live extraction is unavailable --
MOCK_HASHTAGS = [
    {"hashtag_name": "fyp", "hashtag_id": "1",
     "post_count": 50_000_000_000, "view_count": 800_000_000_000},
    {"hashtag_name": "viral", "hashtag_id": "2",
     "post_count": 20_000_000_000, "view_count": 400_000_000_000},
    {"hashtag_name": "foodtok", "hashtag_id": "3",
     "post_count": 5_000_000_000, "view_count": 80_000_000_000},
    {"hashtag_name": "recipe", "hashtag_id": "4",
     "post_count": 3_000_000_000, "view_count": 50_000_000_000},
    {"hashtag_name": "cooking", "hashtag_id": "5",
     "post_count": 2_500_000_000, "view_count": 45_000_000_000},
    {"hashtag_name": "bbq", "hashtag_id": "6",
     "post_count": 800_000_000, "view_count": 12_000_000_000},
    {"hashtag_name": "streetfood", "hashtag_id": "7",
     "post_count": 1_200_000_000, "view_count": 20_000_000_000},
    {"hashtag_name": "foodreview", "hashtag_id": "8",
     "post_count": 900_000_000, "view_count": 15_000_000_000},
    {"hashtag_name": "restaurant", "hashtag_id": "9",
     "post_count": 600_000_000, "view_count": 10_000_000_000},
    {"hashtag_name": "grilling", "hashtag_id": "10",
     "post_count": 400_000_000, "view_count": 6_000_000_000},
]


def _try_extract_next_data(page_content):
    """Try to find hashtag data in a __NEXT_DATA__ script tag."""
    import re
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        page_content, re.DOTALL,
    )
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    # Recursively search for hashtag-like lists
    return _find_hashtag_list(data)


def _find_hashtag_list(obj, depth=0):
    """Recursive search for a list of dicts with hashtag_name keys."""
    if depth > 10:
        return None
    if isinstance(obj, list):
        # Check if this list contains hashtag-like dicts
        if (obj and isinstance(obj[0], dict)
                and "hashtag_name" in obj[0]):
            return obj
        # Recurse into list items
        for item in obj:
            result = _find_hashtag_list(item, depth + 1)
            if result:
                return result
    elif isinstance(obj, dict):
        for key, val in obj.items():
            result = _find_hashtag_list(val, depth + 1)
            if result:
                return result
    return None


def fetch_live_hashtags(geo, timeout_ms=30000):
    """Attempt to load real hashtag data from TikTok Creative Center.

    Returns (hashtags_list, error_string_or_None).
    hashtags_list is a list of dicts with at least hashtag_name.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwright not installed"

    url = f"{TIKTOK_CC_URL}?countryCode={geo}&period=7"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            content = page.content()
            browser.close()
    except Exception as e:
        return None, f"playwright navigation failed: {e}"

    # Strategy 1: __NEXT_DATA__
    hashtags = _try_extract_next_data(content)
    if hashtags:
        return hashtags, None

    # Strategy 2: look for any JSON-like data in script tags
    import re
    for match in re.finditer(
        r'<script[^>]*>(.*?)</script>', content, re.DOTALL
    ):
        try:
            data = json.loads(match.group(1))
            result = _find_hashtag_list(data)
            if result:
                return result, None
        except (json.JSONDecodeError, ValueError):
            continue

    return None, "no hashtag data found in page"


def normalize_to_entities(hashtags, geo, observed_at, is_mock=False):
    """Convert hashtag data into shared-schema entities."""
    entities = []
    for rank_idx, tag in enumerate(hashtags, 1):
        name = tag.get("hashtag_name", "").strip()
        if not name:
            continue

        post_count = tag.get("post_count", 0)
        view_count = tag.get("view_count", 0)

        # Ensure numeric
        if isinstance(post_count, str):
            try:
                post_count = int(post_count)
            except ValueError:
                post_count = 0
        if isinstance(view_count, str):
            try:
                view_count = int(view_count)
            except ValueError:
                view_count = 0

        entities.append(make_entity(
            source="tiktok_creative_center",
            entity_type="hashtag",
            entity_id=f"tt_{tag.get('hashtag_id', name.lower())}",
            entity_name=f"#{name.lower()}",
            category="General",
            region=geo,
            observed_at=observed_at,
            rank=rank_idx,
            primary_value=float(post_count) if post_count else 1.0,
            metrics={
                "post_count": post_count,
                "view_count": view_count,
                "is_mock": is_mock,
            },
            raw={
                "hashtag_name": name,
                "hashtag_id": tag.get("hashtag_id", ""),
                "mock": is_mock,
                **({"mock_reason": "live extraction unavailable; "
                     "using labeled synthetic data"} if is_mock else {}),
            },
        ))

    return entities


def main():
    parser = argparse.ArgumentParser(
        description="Fetch TikTok Creative Center trending hashtags")
    parser.add_argument("--geo", default=DEFAULT_GEO,
        help="Country code: PK, US, IN, GB, AE (default: PK)")
    parser.add_argument("--mock", action="store_true",
        help="Force mock data (skip live fetch)")
    parser.add_argument("--timeout", type=int, default=30000,
        help="Page load timeout in ms (default: 30000)")
    args = parser.parse_args()

    print("TikTok Creative Center - Trending Hashtags")
    print(f"  Region : {args.geo}")
    print(f"  Mode   : {'MOCK' if args.mock else 'live'}\n")

    observed_at = datetime.now(timezone.utc).isoformat()
    is_mock = False

    if args.mock:
        hashtags = MOCK_HASHTAGS
        is_mock = True
        print("  Using mock data (--mock flag)")
    else:
        hashtags, error = fetch_live_hashtags(args.geo, args.timeout)
        if hashtags:
            print(f"  Extracted {len(hashtags)} hashtags from live page")
        else:
            print(f"  Live extraction failed: {error}")
            print("  Falling back to labeled mock data")
            hashtags = MOCK_HASHTAGS
            is_mock = True

    entities = normalize_to_entities(
        hashtags, args.geo, observed_at, is_mock=is_mock)

    source_meta = {
        "region": args.geo,
        "is_mock": is_mock,
        "fetched_at": observed_at,
        "url": f"{TIKTOK_CC_URL}?countryCode={args.geo}&period=7",
    }
    if is_mock:
        source_meta["mock_reason"] = (
            "live extraction unavailable; using labeled synthetic data"
        )

    filename = snapshot_filename("snapshot")
    write_snapshot(filename, "tiktok_creative_center",
                   source_meta, entities)

    print(f"\n  Saved {len(entities)} entities -> {filename}")
    if is_mock:
        print("  NOTE: These are MOCK entities. TikTok Creative Center")
        print("  data shape is not yet confirmed against the live page.")


if __name__ == "__main__":
    main()
