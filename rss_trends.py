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

Articles with a parseable published date older than --max-age-days are dropped, so
this reflects ongoing/current trends rather than whatever a slow-moving feed happened
to publish weeks ago. Articles with no parseable date are kept (undated is not the
same as stale -- most feeds list newest-first regardless).

Output: snapshot_<timestamp>.json in the shared schema.

Usage:
    python rss_trends.py
    python rss_trends.py --max-per-feed 10
    python rss_trends.py --max-age-days 2
"""

import argparse
import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from snapshot_schema import make_entity, snapshot_filename, write_snapshot

try:
    import feedparser
except ImportError:
    feedparser = None


# -- Feed configuration --
# Organized by category. Each entry is (source_name, feed_url).
#
# Every feed below was verified live-and-publishing before being added.
# A lot of well-known outlets (Serious Eats, Eater, Gulf News, Khaleej
# Times, Well+Good, Healthline, Adweek) either 404, hard-block, or return
# nothing but stale entries, so they are deliberately NOT here -- a dead
# feed silently contributes zero articles and just makes the pool thinner.
#
# Coverage is spread across the categories the businesses using this tool
# actually operate in (food, beauty, fashion, fitness, retail/B2B), plus
# regional news, so relevance matching has something real to work with
# rather than an all-tech pool.
RSS_FEEDS = {
    "Food & Beverage": [
        ("Bon Appetit", "https://www.bonappetit.com/feed/rss"),
        ("The Kitchn", "https://www.thekitchn.com/main.rss"),
        ("Delish", "https://www.delish.com/rss/all.xml/"),
    ],
    "Fashion & Beauty": [
        ("Vogue", "https://www.vogue.com/feed/rss"),
        ("Allure", "https://www.allure.com/feed/rss"),
        ("Refinery29 Beauty",
         "https://www.refinery29.com/en-us/beauty/rss.xml"),
        ("Glossy", "https://www.glossy.co/feed/"),
    ],
    "Health & Wellness": [
        ("Runners World", "https://www.runnersworld.com/rss/all.xml/"),
    ],
    "Business & Finance": [
        ("Entrepreneur", "https://www.entrepreneur.com/latest.rss"),
        ("SaaStr", "https://www.saastr.com/feed/"),
        ("Modern Retail", "https://www.modernretail.co/feed/"),
    ],
    "Technology": [
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("Hacker News", "https://news.ycombinator.com/rss"),
    ],
    "Lifestyle": [
        ("Lifehacker", "https://lifehacker.com/rss"),
    ],
    "Entertainment": [
        ("Variety", "https://variety.com/feed/"),
    ],
}

# Regional news, selected by --geo. Local signal is worth a lot for a
# small business (a fuel price hike, a public holiday, a cricket final are
# all real campaign material), but only for the country it applies to --
# a London label has no use for Karachi petrol prices topping its list.
#
# No usable AE feed exists: Gulf News, Khaleej Times, The National, Zawya,
# Arabian Business and Emirates247 all 404, hard-block, or return nothing.
# AE therefore falls back to international outlets, which do cover the
# region, rather than shipping a feed that silently yields zero.
REGIONAL_FEEDS = {
    "PK": [
        ("Dawn", "https://www.dawn.com/feeds/home"),
        ("Dawn Latest", "https://www.dawn.com/feeds/latest-news"),
        ("Express Tribune", "https://tribune.com.pk/feed/home"),
    ],
    "GB": [
        ("BBC News", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("Guardian UK", "https://www.theguardian.com/uk/rss"),
        ("Guardian Lifestyle",
         "https://www.theguardian.com/uk/lifeandstyle/rss"),
    ],
    "US": [
        ("NPR News", "https://feeds.npr.org/1001/rss.xml"),
        ("CBS News", "https://www.cbsnews.com/latest/rss/main"),
        ("NBC News", "http://feeds.nbcnews.com/nbcnews/public/news"),
    ],
    "IN": [
        ("NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories"),
    ],
    "AE": [
        ("BBC News", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ],
}

# Used when no geo is given (the UI's "Worldwide" option).
DEFAULT_REGIONAL = [
    ("BBC News", "https://feeds.bbci.co.uk/news/rss.xml"),
]

REGIONAL_CATEGORY = "News & Politics"

# Some hosts block the default feedparser agent; others reject this one.
# fetch_feed() tries both rather than losing a feed to either failure mode.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")

# Per-category overrides for max articles per feed.
# Daily newspapers publish an order of magnitude more often than a food
# or fashion title, so at a uniform cap they crowd the pool with
# parliamentary and diplomatic minutiae that no small business is going
# to build a campaign on. Capping them keeps the genuinely useful local
# signal (fuel prices, cricket, holidays) without the flood.
CATEGORY_LIMITS = {
    "News & Politics": 6,
}

DEFAULT_MAX_PER_FEED = 15
DEFAULT_MAX_AGE_DAYS = 3


def strip_html(text):
    """Crude tag strip -- feed summaries are frequently HTML fragments."""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", str(text))
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()[:280]


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


def fetch_feed(url):
    """Parse one feed, trying a browser UA first then a bare request.

    Neither strategy works everywhere: some hosts 403 the default
    feedparser agent, others reject the browser UA or the requests
    client. Whichever returns entries wins; empty is treated as failure
    so the second strategy still gets a turn.
    """
    try:
        import requests
        resp = requests.get(url, timeout=25,
                            headers={"User-Agent": BROWSER_UA})
        if resp.status_code == 200:
            parsed = feedparser.parse(resp.content)
            if parsed.entries:
                return parsed
    except Exception:
        pass

    try:
        parsed = feedparser.parse(url)
        if parsed.entries:
            return parsed
    except Exception:
        pass

    return None


def build_feed_map(geo=""):
    """Topical feeds plus the regional feeds for this country."""
    feeds = {k: list(v) for k, v in RSS_FEEDS.items()}
    geo = (geo or "").strip().upper()
    regional = REGIONAL_FEEDS.get(geo, DEFAULT_REGIONAL)
    if regional:
        feeds.setdefault(REGIONAL_CATEGORY, [])
        feeds[REGIONAL_CATEGORY].extend(regional)
    return feeds


def fetch_rss_articles(max_per_feed=DEFAULT_MAX_PER_FEED,
                        max_age_days=DEFAULT_MAX_AGE_DAYS,
                        geo=""):
    """Fetch articles from all configured RSS feeds.

    Returns a list of raw article dicts before normalization. Articles
    older than max_age_days (by published date, when parseable) are
    dropped -- this keeps the signal to ongoing/current trends rather
    than whatever a slow-moving feed happened to publish weeks ago.
    """
    if feedparser is None:
        print("  ERROR: feedparser is not installed.")
        print("  Install with: pip install feedparser")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    feed_map = build_feed_map(geo)
    articles = []
    total_feeds = sum(len(v) for v in feed_map.values())
    fetched = 0
    errors = 0
    stale_skipped = 0

    for category, feeds in feed_map.items():
        for source_name, url in feeds:
            try:
                feed = fetch_feed(url)
                if feed is None:
                    errors += 1
                    continue
                count = 0
                limit = CATEGORY_LIMITS.get(category, max_per_feed)
                for entry in feed.entries[:limit]:
                    title = html.unescape(
                        entry.get("title", "").strip())
                    if not title:
                        continue
                    published = _parse_published(entry)
                    if published and published < cutoff:
                        stale_skipped += 1
                        continue
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
          f"({errors} errors), {len(articles)} articles within "
          f"{max_age_days}d ({stale_skipped} older articles skipped)")
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
                # Short plain-text gloss, carried downstream so the
                # relevance stage can judge an article on more than its
                # headline. Falls back to the outlet name when a feed
                # ships no summary.
                "context": strip_html(info["summary"]) or (
                    f"Article from {', '.join(sorted(set(info['sources'])))}"),
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
    parser.add_argument("--max-age-days", type=int,
                        default=DEFAULT_MAX_AGE_DAYS,
                        help="Drop articles older than this many days, "
                             "when a published date is available "
                             "(default: 3)")
    parser.add_argument("--geo", default="",
                        help="Country code (PK, GB, US, IN, AE) selecting "
                             "which regional news feeds to include "
                             "alongside the topical ones")
    args = parser.parse_args()

    feed_map = build_feed_map(args.geo)
    total = sum(len(v) for v in feed_map.values())

    print("Fetching RSS Feed Articles")
    print(f"  Max per feed: {args.max_per_feed}")
    print(f"  Max age     : {args.max_age_days} day(s)")
    print(f"  Region      : {(args.geo or 'worldwide').upper()}")
    print(f"  Configured feeds: {total}\n")

    articles = fetch_rss_articles(args.max_per_feed, args.max_age_days,
                                  args.geo)
    if not articles:
        print("  No articles fetched.")
        return

    observed_at = datetime.now(timezone.utc).isoformat()
    entities = normalize_to_entities(articles, observed_at)

    source_meta = {
        "total_articles": len(articles),
        "unique_topics": len(entities),
        "feeds_configured": total,
        "geo": (args.geo or "").upper(),
        "fetched_at": observed_at,
    }

    filename = snapshot_filename("snapshot", "rss")
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
