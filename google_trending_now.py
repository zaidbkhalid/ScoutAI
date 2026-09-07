"""
google_trending_now.py
----------------------
Fetches Google's live "Daily Search Trends" -- what people are actually
searching for right now, per country.

Source: https://trends.google.com/trending/rss?geo=<CC>
    This is Google's own public RSS feed. No API key, no auth, no quota.

WHY RSS AND NOT PYTRENDS:
    This script previously used pytrends' realtime_trending_searches(),
    which now fails with HTTP 404 -- Google retired the private endpoint
    pytrends was scraping. The public RSS feed above is still served and
    is actually richer for our purposes: it carries an approximate search
    volume per term plus the news story driving it.

WHAT WE GET PER TREND:
    - the search term itself
    - approx_traffic ("200+", "5000+")  -> real numeric signal, used as
      primary_value so trend_scoring.py can rank and compute velocity
      against it rather than a flat presence indicator
    - the news headline behind the spike -> carried as `context` so the
      relevance stage can reason about WHY something is trending instead
      of guessing from a bare term like "argentina"

Output: snapshot_google_trending_now_<timestamp>.json (shared schema)

Usage:
    python google_trending_now.py
    python google_trending_now.py --geo PK
"""

import re
import argparse
import requests
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from snapshot_schema import make_entity, snapshot_filename, write_snapshot

try:
    import feedparser
except ImportError:
    feedparser = None


DEFAULT_GEO = "PK"
TRENDS_RSS = "https://trends.google.com/trending/rss?geo={geo}"

# The RSS feed is per-country; there is no worldwide variant. An empty geo
# (the UI's "Worldwide" option) falls back to US as the broadest proxy.
FALLBACK_GEO = "US"


def parse_traffic(raw):
    """'5,000+' -> 5000.0.  Returns 1.0 when unparseable (presence only)."""
    if not raw:
        return 1.0
    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        return 1.0
    try:
        return float(digits)
    except ValueError:
        return 1.0


def parse_published(entry):
    raw = entry.get("published")
    if raw:
        try:
            return parsedate_to_datetime(raw)
        except Exception:
            pass
    parsed = entry.get("published_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def fetch_trending_now(geo):
    """Fetch and parse Google's daily search trends RSS for one country."""
    if feedparser is None:
        print("  ERROR: feedparser is not installed.")
        print("  Install with: pip install feedparser")
        return []

    url = TRENDS_RSS.format(geo=geo)
    try:
        resp = requests.get(
            url, timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ScoutAI/1.0)"})
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"  ERROR: could not fetch Google Trends RSS: {e}")
        return []

    feed = feedparser.parse(resp.content)

    trends = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        trends.append({
            "title": title,
            "approx_traffic": entry.get("ht_approx_traffic", ""),
            "published": parse_published(entry),
            "news_title": (entry.get("ht_news_item_title") or "").strip(),
            "news_source": (entry.get("ht_news_item_source") or "").strip(),
            "news_url": (entry.get("ht_news_item_url") or "").strip(),
        })

    return trends


def normalize_to_entities(trends, geo, observed_at):
    """Convert raw trending-now data into shared-schema entities."""
    entities = []
    for idx, item in enumerate(trends, 1):
        title = item.get("title", "").strip()
        if not title:
            continue

        traffic = parse_traffic(item.get("approx_traffic"))

        # Real publish time when available, so age decay reflects when the
        # spike actually happened rather than when we happened to fetch it.
        published = item.get("published")
        seen_at = published.isoformat() if published else observed_at

        # One-line reason this term is spiking, for the relevance stage.
        context = item.get("news_title", "")
        if context and item.get("news_source"):
            context = f"{context} ({item['news_source']})"

        entities.append(make_entity(
            source="google_trending_now",
            entity_type="trending_topic",
            entity_id=f"gt_rt_{title.lower().replace(' ', '_')[:50]}",
            entity_name=title.lower().strip(),
            category="General",
            region=geo,
            observed_at=seen_at,
            rank=idx,
            primary_value=traffic,
            metrics={
                "approx_traffic": traffic,
                "approx_traffic_label": item.get("approx_traffic", ""),
            },
            raw={
                "title": title,
                "context": context,
                "news_title": item.get("news_title", ""),
                "news_source": item.get("news_source", ""),
                "news_url": item.get("news_url", ""),
                "published_at": published.isoformat() if published else "",
            },
        ))
    return entities


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geo", default=DEFAULT_GEO,
        help="Country code: PK, US, IN, GB, AE (default: PK)")
    args = parser.parse_args()

    geo = (args.geo or "").strip().upper() or FALLBACK_GEO
    if not (args.geo or "").strip():
        print(f"  No geo given (worldwide); Google's trends RSS is "
              f"per-country, using {FALLBACK_GEO} as the broadest proxy.")

    print(f"Fetching Google Daily Search Trends")
    print(f"  Geo: {geo}")
    print(f"  Source: public Trends RSS (no API key required)\n")

    trends = fetch_trending_now(geo)
    if not trends:
        print("  No trends returned.")
        return

    observed_at = datetime.now(timezone.utc).isoformat()
    entities = normalize_to_entities(trends, geo, observed_at)

    source_meta = {
        "geo": geo,
        "total_trends": len(trends),
        "fetched_at": observed_at,
        "endpoint": TRENDS_RSS.format(geo=geo),
    }

    filename = snapshot_filename("snapshot", "google_trending_now")
    write_snapshot(filename, "google_trending_now", source_meta, entities)

    print(f"  Saved {len(entities)} entities -> {filename}\n")

    top = sorted(entities, key=lambda e: e["primary_value"], reverse=True)[:8]
    print("  Top trending searches by volume:")
    for e in top:
        label = e["metrics"].get("approx_traffic_label") or "?"
        ctx = e["raw"].get("news_title", "")[:48]
        print(f"    [{label:>7}]  {e['entity_name'][:38]:38}  {ctx}")


if __name__ == "__main__":
    main()
