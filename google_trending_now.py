"""
google_trending_now.py
----------------------
Fetches real-time trending searches (no keyword filter).

Output: snapshot_<timestamp>.json in the shared schema, containing
normalized trending-topic entities.

Usage:
    python google_trending_now.py
    python google_trending_now.py --geo PK
"""

import json
import time
import argparse
from datetime import datetime, timezone
from pytrends.request import TrendReq
from snapshot_schema import make_entity, snapshot_filename, write_snapshot


DEFAULT_GEO = "PK"


def request_with_retry(fn, label, retries=4, base_delay=10.0):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if "429" in str(e) or "TooManyRequests" in str(e):
                wait = base_delay * (2 ** attempt)
                print(f"  Rate limited [{label}]. Waiting {int(wait)}s "
                      f"(retry {attempt+1}/{retries})...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Failed to fetch [{label}] after {retries} retries.")


def fetch_trending_now(geo):
    pytrends = TrendReq(hl="en-US", tz=300)
    df = request_with_retry(
        lambda: pytrends.realtime_trending_searches(pn=geo),
        "realtime_trending_searches")

    trends = []
    for _, row in df.iterrows():
        trend = {
            "title"       : row.get("title", ""),
            "entity_names": row.get("entityNames", []),
            "articles"    : [],
        }
        articles = row.get("articles", [])
        if isinstance(articles, list):
            for a in articles[:5]:
                if isinstance(a, dict):
                    trend["articles"].append({
                        "title"  : a.get("articleTitle", ""),
                        "source" : a.get("source", ""),
                        "url"    : a.get("url", ""),
                        "snippet": a.get("snippet", ""),
                    })
        trends.append(trend)

    return trends


def normalize_to_entities(trends, geo, observed_at):
    """Convert raw trending-now data into shared-schema entities."""
    entities = []
    for idx, item in enumerate(trends, 1):
        title = item.get("title", "").strip()
        if not title:
            continue
        entities.append(make_entity(
            source="google_trending_now",
            entity_type="trending_topic",
            entity_id=f"gt_rt_{title.lower().replace(' ', '_')[:50]}",
            entity_name=title.lower().strip(),
            category="General",
            region=geo,
            observed_at=observed_at,
            rank=idx,
            primary_value=1.0,  # presence indicator
            metrics={
                "article_count": len(item.get("articles", [])),
            },
            raw={
                "title": title,
                "entity_names": item.get("entity_names", []),
                "articles": item.get("articles", []),
            },
        ))
    return entities


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geo", default=DEFAULT_GEO,
        help="Country code: PK, US, IN, GB, AE (default: PK)")
    args = parser.parse_args()

    print(f"Fetching Realtime Trending Searches")
    print(f"  Geo: {args.geo}\n")

    trends = fetch_trending_now(args.geo)
    observed_at = datetime.now(timezone.utc).isoformat()
    entities = normalize_to_entities(trends, args.geo, observed_at)

    source_meta = {
        "geo": args.geo,
        "total_trends": len(trends),
        "fetched_at": observed_at,
    }

    filename = snapshot_filename("snapshot")
    write_snapshot(filename, "google_trending_now", source_meta, entities)

    print(f"  Saved {len(entities)} entities -> {filename}")


if __name__ == "__main__":
    main()
