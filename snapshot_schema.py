"""
snapshot_schema.py
------------------
Shared snapshot schema for all trend data sources.

Every fetcher normalizes its output into this common shape so that
downstream consumers (trend_scoring.py, analyze_trends.py) never need
to know which platform the data came from.

Snapshot file format (snapshot_<timestamp>.json):
{
    "schema_version": "1.0",
    "source": "google_trends" | "google_trending_now" | ...
    "source_meta": { ... fetcher-specific metadata ... },
    "entities": [
        {
            "source": "...",
            "entity_type": "keyword" | "video" | "article_topic" | "hashtag" | "trending_topic",
            "entity_id": "...",
            "entity_name": "...",
            "category": "...",
            "region": "...",
            "observed_at": "ISO8601",
            "rank": int | null,
            "primary_value": float,
            "metrics": { ... },
            "raw": { ... }
        }
    ]
}

primary_value is the single numeric signal used for velocity scoring:
  - keyword:        latest search interest (0-100+)
  - related_query:  query value (Breakout mapped to 5000)
  - trending_topic: 1.0  (presence indicator)
  - video:          view_count
  - article_topic:  1.0  (presence indicator)
  - hashtag:        post_count or 1.0
"""

from datetime import datetime, timezone
import json

SCHEMA_VERSION = "1.0"

VALID_SOURCES = (
    "google_trends",
    "google_trending_now",
    "youtube_trending",
    "rss",
    "tiktok_creative_center",
)

VALID_ENTITY_TYPES = (
    "keyword",
    "related_query",
    "trending_topic",
    "video",
    "article_topic",
    "hashtag",
)

# Category vocabulary reused across all sources.
CATEGORIES = {
    "food":             "Food & Beverage",
    "food_beverage":    "Food & Beverage",
    "technology":       "Technology",
    "tech":             "Technology",
    "entertainment":    "Entertainment",
    "sports":           "Sports",
    "health":           "Health & Wellness",
    "wellness":         "Health & Wellness",
    "fashion":          "Fashion & Beauty",
    "beauty":           "Fashion & Beauty",
    "business":         "Business & Finance",
    "finance":          "Business & Finance",
    "news":             "News & Politics",
    "politics":         "News & Politics",
    "lifestyle":        "Lifestyle",
    "general":          "General",
}

# YouTube videoCategoryId -> project category
YOUTUBE_CATEGORY_MAP = {
    "1":  "Entertainment",
    "2":  "Entertainment",
    "10": "Entertainment",
    "15": "Entertainment",
    "17": "Sports",
    "19": "Entertainment",
    "20": "Entertainment",
    "22": "Entertainment",
    "23": "Entertainment",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Fashion & Beauty",
    "27": "Lifestyle",
    "28": "Technology",
    "29": "Lifestyle",
}


def make_entity(
    source,
    entity_type,
    entity_id,
    entity_name,
    category="General",
    region="",
    observed_at="",
    rank=None,
    primary_value=1.0,
    metrics=None,
    raw=None,
):
    """Create a single entity dict in the shared schema."""
    return {
        "source": source,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "category": category,
        "region": region,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "rank": rank,
        "primary_value": primary_value,
        "metrics": metrics or {},
        "raw": raw or {},
    }


def snapshot_filename(prefix="snapshot"):
    """Generate a timestamped snapshot filename (UTC)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{ts}.json"


def write_snapshot(filepath, source, source_meta, entities):
    """Write a complete snapshot file in the shared schema."""
    output = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "source_meta": source_meta,
        "entities": entities,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
