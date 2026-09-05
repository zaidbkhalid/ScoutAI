"""
rss_trends.py
-------------
Fetches latest articles from configured RSS feeds and normalizes them
into the shared snapshot schema.

No API credentials required -- uses feedparser to parse public RSS/Atom feeds.

NOTE ON METRICS:
    RSS does not carry a virality metric the way social platforms do.
    There is no "like count" or "view count" on an RSS entry.
    The closest proxy we have is:
      - How many configured feeds mention a given topic within the
        recency window (feed_count in metrics).
      - Article recency itself as the freshness signal (published_at).
    primary_value is set to 1.0 (presence indicator) for every article.
    The scoring engine's velocity computation will measure whether a
    topic is appearing in MORE feeds over time, which is the RSS
    equivalent of "rising".

Feed configuration:
    RSS_FEEDS dict below maps category -> list of (source_name, feed_url).
    Add or remove feeds as needed. The starter list targets food/beverage
    and general interest sources that are active as of 2026.

Output: snapshot_<timestamp>.json in the shared schema.

Usage:
    python rss_trends.py
    python rss_trends.py --max-per-feed 10
"""

import argparse
import hashlib
import html
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from snapshot_schema import make_entity, snapshot_filename, write_snapshot

try:
    import feedparser
except ImportError:
    feedparser = None


# -- Feed configuration --
# Organized by category. Each entry is (source_name, feed_url).
# These are real, currently-active public RSS/Atom feeds.
RSS_FEEDS = {
    "Food & Beverage": [
        ("Serious Eats", "https://www.seriouseats.com/rss"),
        ("Food and Wine", "https://www.foodandwine.com/rss"),
        ("Eater", "https://www.eater.com/rss/index.xml"),
        ("Bon Appetit", "https://www.bonappetit.com/feed/rss"),
        ("NYT Cooking", "https://cooking.nytimes.com/rss"),
    ],
    "Technology": [
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("Hacker News", "https://news.ycombinator.com/rss"),
    ],
    "Business & Finance": [
        ("Entrepreneur", "https://www.entrepreneur.com/latest.rss"),
    ],
    "Entertainment": [
        ("Variety", "https://variety.com/feed/"),
    ],
    "Lifestyle": [
        ("Lifehacker", "https://lifehacker.com/rss"),
    ],
}

DEFAULT_MAX_PER_FEED = 15


def _parse_published(entry):
    """Try to parse a published date from an RSS entry."""
    for field in ("published", "updated", "created"):
        raw = entry.get(field)
        if raw:
            try:
                return parsedate_to_datetime(raw)
            except Exception:
                pass
            # feedparser also provides a parsed struct_time
            parsed = entry.get(f"{field}_parsed")
            if parsed:
                try:
                    return datetime(*parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
    return None


def fetch_rss_articles(max_per_feed=DEFAULT_MAX_PER_FEED):
    """Fetch articles from all configured RSS feeds.

    Returns a list of raw article dicts before normalization.
    """
    if feedparser is None:
        print("  ERROR: feedparser is not installed.")
        print("  Install with: pip install feedparser")
        return []

    articles = []
    total_feeds = sum(len(v) for v in RSS_FEEDS.values())
    fetched = 0
    errors = 0

    for category, feeds in RSS_FEEDS.items():
        for source_name, url in feeds:
            try:
                feed = feedparser.parse(url)
                count = 0
                for entry in feed.entries[:max_per_feed]:
                    title = html.unescape(
                        entry.get("title", "").strip())
                    if not title:
                        continue
                    published = _parse_published(entry)
                    articles.append({
                        "title": title,
                        "link": entry.get("link", ""),
                        "source_name": source_name,
                        "category": category,
                        "published": published,
                        "summary": entry.get("summary", "")[:200],
                    })
                    count += 1
                fetched += 1
            except Exception:
                errors += 1

    print(f"  Fetched {fetched}/{total_feeds} feeds "
          f"({errors} errors), {len(articles)} articles")
    return articles


def normalize_to_entities(articles, observed_at):
    """Convert raw RSS articles into shared-schema entities.

    Each unique article title becomes one entity. If the same title
    appears across multiple feeds, they are merged and feed_count
    is incremented.
    """
    # Deduplicate by normalized title
    seen = {}
    for article in articles:
        key = article["title"].lower().strip()
        if not key:
            continue
        if key in seen:
            seen[key]["feed_count"] += 1
            seen[key]["sources"].append(article["source_name"])
            # Keep the earliest published date
            if article["published"] and (
                not seen[key]["published"]
                or article["published"] < seen[key]["published"]
            ):
                seen[key]["published"] = article["published"]
        else:
            seen[key] = {
                "title": article["title"],
                "category": article["category"],
                "link": article["link"],
                "feed_count": 1,
                "sources": [article["source_name"]],
                "published": article["published"],
                "summary": article["summary"],
            }

    entities = []
    for key, info in seen.items():
        # Use SHA hash of title as stable entity ID
        eid = hashlib.sha256(key.encode()).hexdigest()[:16]

        pub_at = ""
        if info["published"]:
            pub_at = info["published"].isoformat()

        entities.append(make_entity(
            source="rss",
            entity_type="article_topic",
            entity_id=f"rss_{eid}",
            entity_name=key,
            category=info["category"],
            region="global",
            observed_at=pub_at or observed_at,
            primary_value=1.0,  # presence indicator; see module docstring
            metrics={
                "feed_count": info["feed_count"],
            },
            raw={
                "title": info["title"],
                "link": info["link"],
                "sources": list(set(info["sources"])),
                "summary": info["summary"],
                "published_at": pub_at,
            },
        ))

    return entities


def main():
    parser = argparse.ArgumentParser(
        description="Fetch trends from RSS feeds")
    parser.add_argument("--max-per-feed", type=int,
                        default=DEFAULT_MAX_PER_FEED,
                        help="Max articles per feed (default: 15)")
    args = parser.parse_args()

    print("Fetching RSS Feed Articles")
    print(f"  Max per feed: {args.max_per_feed}")
    total = sum(len(v) for v in RSS_FEEDS.values())
    print(f"  Configured feeds: {total}\n")

    articles = fetch_rss_articles(args.max_per_feed)
    if not articles:
        print("  No articles fetched.")
        return

    observed_at = datetime.now(timezone.utc).isoformat()
    entities = normalize_to_entities(articles, observed_at)

    source_meta = {
        "total_articles": len(articles),
        "unique_topics": len(entities),
        "feeds_configured": total,
        "fetched_at": observed_at,
    }

    filename = snapshot_filename("snapshot")
    write_snapshot(filename, "rss", source_meta, entities)

    print(f"\n  Saved {len(entities)} entities -> {filename}")
    if entities:
        # Show a few multi-feed topics first
        multi = sorted(
            entities,
            key=lambda e: e["metrics"]["feed_count"],
            reverse=True,
        )[:5]
        print(f"\n  Top topics by feed coverage:")
        for e in multi:
            fc = e["metrics"]["feed_count"]
            print(f"    [{fc} feeds]  {e['raw']['title'][:55]}")


if __name__ == "__main__":
    main()
