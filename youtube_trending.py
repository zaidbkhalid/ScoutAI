"""
youtube_trending.py
-------------------
Fetches trending videos from YouTube Data API v3 (videos.list, chart=mostPopular).

Requires YOUTUBE_API_KEY env var (free from Google Cloud Console with the
YouTube Data API v3 enabled -- no billing required for the 10,000 units/day
free quota tier; videos.list costs 1 unit per call).

Output: snapshot_<timestamp>.json in the shared schema.

Usage:
    python youtube_trending.py
    python youtube_trending.py --geo PK
    python youtube_trending.py --geo US --max-results 10
"""

import os
import argparse
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from snapshot_schema import (
    make_entity, snapshot_filename, write_snapshot, YOUTUBE_CATEGORY_MAP,
)

load_dotenv()

YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"
DEFAULT_GEO = "PK"
DEFAULT_MAX_RESULTS = 25


def fetch_youtube_trending(api_key, region_code, max_results=25):
    """Fetch mostPopular videos from YouTube Data API v3."""
    params = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "regionCode": region_code,
        "maxResults": min(max_results, 50),
        "key": api_key,
    }
    resp = requests.get(YOUTUBE_API_URL, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def normalize_to_entities(api_response, region_code, observed_at):
    """Convert YouTube API response into shared-schema entities."""
    entities = []
    items = api_response.get("items", [])

    for rank_idx, item in enumerate(items, 1):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})

        video_id = item.get("id", "")
        title = snippet.get("title", "").strip()
        if not title:
            continue

        # Map YouTube category to project taxonomy
        yt_cat_id = str(snippet.get("categoryId", ""))
        category = YOUTUBE_CATEGORY_MAP.get(yt_cat_id, "Entertainment")

        # Parse numeric stats (API returns them as strings)
        view_count = int(stats.get("viewCount", 0))
        like_count = int(stats.get("likeCount", 0))
        comment_count = int(stats.get("commentCount", 0))

        entities.append(make_entity(
            source="youtube_trending",
            entity_type="video",
            entity_id=f"yt_{video_id}",
            entity_name=title.lower().strip(),
            category=category,
            region=region_code,
            observed_at=observed_at,
            rank=rank_idx,
            primary_value=float(view_count),
            metrics={
                "view_count": view_count,
                "like_count": like_count,
                "comment_count": comment_count,
            },
            raw={
                "video_id": video_id,
                "title": title,
                "channel": snippet.get("channelTitle", ""),
                "published_at": snippet.get("publishedAt", ""),
                "category_id": yt_cat_id,
                "tags": snippet.get("tags", [])[:5],
                "thumbnail": snippet.get("thumbnails", {})
                    .get("medium", {}).get("url", ""),
            },
        ))

    return entities


def main():
    parser = argparse.ArgumentParser(
        description="Fetch YouTube trending videos")
    parser.add_argument("--geo", default=DEFAULT_GEO,
        help="Region code: PK, US, IN, GB, AE (default: PK)")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS,
        help="Max videos to fetch (1-50, default: 25)")
    args = parser.parse_args()

    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        print("  ERROR: YOUTUBE_API_KEY not set.")
        print("  Get a free key at: https://console.cloud.google.com/")
        print("  Enable 'YouTube Data API v3', then add to .env:")
        print("    YOUTUBE_API_KEY=your_key_here")
        return

    print(f"Fetching YouTube Trending Videos")
    print(f"  Region     : {args.geo}")
    print(f"  Max results: {args.max_results}\n")

    try:
        api_response = fetch_youtube_trending(
            api_key, args.geo, args.max_results)
    except requests.exceptions.HTTPError as e:
        print(f"  ERROR: YouTube API request failed: {e}")
        if e.response is not None:
            err = e.response.json().get("error", {})
            print(f"  Detail: {err.get('message', 'unknown')}")
        return
    except Exception as e:
        print(f"  ERROR: {e}")
        return

    observed_at = datetime.now(timezone.utc).isoformat()
    entities = normalize_to_entities(api_response, args.geo, observed_at)

    total_results = api_response.get("pageInfo", {}).get("totalResults", 0)
    source_meta = {
        "region_code": args.geo,
        "total_available": total_results,
        "fetched_count": len(entities),
        "fetched_at": observed_at,
    }

    filename = snapshot_filename("snapshot")
    write_snapshot(filename, "youtube_trending", source_meta, entities)

    print(f"  Saved {len(entities)} entities -> {filename}")
    if entities:
        print(f"\n  Top 5 trending videos:")
        for e in entities[:5]:
            views = e["metrics"]["view_count"]
            print(f"    #{e['rank']:2d}  {views:>12,} views  "
                  f"{e['raw']['title'][:50]}")


if __name__ == "__main__":
    main()
