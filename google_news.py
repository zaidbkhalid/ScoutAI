"""
google_news.py
--------------
Fetches Google News top stories per country, plus its topic sections
(Business, Technology, Entertainment, Sports, Health).

Source: https://news.google.com/rss?hl=en-<CC>&gl=<CC>&ceid=<CC>:en
    Google's own public RSS. No API key, no auth, no quota.

WHY THIS AND NOT MORE HAND-PICKED FEEDS:
    rss_trends.py reads a curated set of outlets, and every one of them
    has to be found, verified and kept alive by hand -- which is why the
    UAE has none at all. Google News aggregates hundreds of outlets per
    country and already clusters coverage of the same story, so one feed
    per section replaces dozens of individual ones.

WHAT WE GET PER STORY:
    - the headline and the outlet that ran it
    - how many related articles Google grouped under it -> used as
      primary_value, since a story five outlets are covering is a
      stronger signal than one only a single outlet ran
    - the section it came from -> the category, so a bakery's relevance
      pool gets business and lifestyle stories, not only front-page news

UAE:
    Google News has no English UAE edition -- gl=AE serves the US edition
    verbatim (checked: 38 of 38 headlines identical). Region-scoped search
    feeds are used instead, which do return UAE stories.

Output: snapshot_google_news_<timestamp>.json (shared schema)

Usage:
    python google_news.py
    python google_news.py --geo PK
    python google_news.py --geo AE --max-age-days 2
"""

import re
import html
import hashlib
import argparse
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from snapshot_schema import make_entity, snapshot_filename, write_snapshot

try:
    import feedparser
except ImportError:
    feedparser = None


DEFAULT_GEO = "PK"
DEFAULT_MAX_AGE_DAYS = 3
# Each section serves ~40-70 stories. Taking all of them would let this
# one source outnumber every other source combined; the top 25 per
# section are the ones Google itself ranks highest.
DEFAULT_MAX_PER_SECTION = 25
# No worldwide edition exists; an empty geo falls back to the US edition
# as the broadest proxy, same as google_trending_now.py.
FALLBACK_GEO = "US"

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

EDITION = "hl=en-{geo}&gl={geo}&ceid={geo}:en"
TOP_URL = "https://news.google.com/rss?" + EDITION
TOPIC_URL = ("https://news.google.com/rss/headlines/section/topic/{topic}?"
             + EDITION)
SEARCH_URL = ("https://news.google.com/rss/search?q={query}"
              "&hl=en-US&gl=US&ceid=US:en")

# Google News section -> project category (see snapshot_schema.CATEGORIES)
# SCIENCE is left out: it is the same global content in every edition and,
# in a live PK run, crowded the country's top three with space and
# astronomy stories no small business can act on.
TOPICS = {
    "BUSINESS":      "Business & Finance",
    "TECHNOLOGY":    "Technology",
    "ENTERTAINMENT": "Entertainment",
    "SPORTS":        "Sports",
    "HEALTH":        "Health & Wellness",
}
TOP_CATEGORY = "News & Politics"

# Countries without a real English edition get search feeds instead.
# `when:2d` keeps Google's own recency filter in step with ours.
SEARCH_EDITIONS = {
    "AE": [
        (TOP_CATEGORY, 'UAE OR Dubai OR "Abu Dhabi" when:2d'),
        ("Business & Finance", '(UAE OR Dubai) (business OR economy OR retail) when:2d'),
        ("Lifestyle", '(UAE OR Dubai) (food OR restaurant OR fashion OR beauty OR event) when:2d'),
        ("Entertainment", '(UAE OR Dubai) (concert OR film OR festival OR celebrity) when:2d'),
    ],
}


def build_feed_list(geo):
    """[(category, url)] for one country."""
    if geo in SEARCH_EDITIONS:
        return [(cat, SEARCH_URL.format(query=quote_plus(q)))
                for cat, q in SEARCH_EDITIONS[geo]]
    feeds = [(TOP_CATEGORY, TOP_URL.format(geo=geo))]
    feeds += [(cat, TOPIC_URL.format(topic=topic, geo=geo))
              for topic, cat in TOPICS.items()]
    return feeds


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


def split_headline(title, source_name):
    """'Headline - Outlet' -> ('Headline', 'Outlet').

    Google appends the outlet to every title; left in, the same story run
    by two outlets would never dedupe and every entity name would end in
    a publisher.
    """
    title = (title or "").strip()
    if source_name and title.endswith(f" - {source_name}"):
        return title[: -len(source_name) - 3].strip(), source_name
    if " - " in title:
        head, _, tail = title.rpartition(" - ")
        return head.strip(), source_name or tail.strip()
    return title, source_name


def cluster_headlines(summary_html):
    """Headlines of the related articles Google grouped with this story."""
    # Each <li> is <a>headline</a>&nbsp;<font>outlet</font>; only the
    # anchor text is the headline.
    items = re.findall(r"<li>\s*<a[^>]*>(.*?)</a>", summary_html or "", re.DOTALL)
    out = []
    for item in items:
        text = re.sub(r"<[^>]+>", " ", item)
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        if text:
            out.append(text)
    return out


def fetch_feed(url):
    try:
        resp = requests.get(url, timeout=25,
                            headers={"User-Agent": BROWSER_UA})
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return None, str(e)
    parsed = feedparser.parse(resp.content)
    if not parsed.entries:
        return None, "feed returned no entries"
    return parsed, None


def fetch_google_news(geo, max_age_days=DEFAULT_MAX_AGE_DAYS,
                      max_per_section=DEFAULT_MAX_PER_SECTION):
    """Fetch every section for one country. Returns (stories, errors)."""
    if feedparser is None:
        print("  ERROR: feedparser is not installed.")
        print("  Install with: pip install feedparser")
        return [], ["feedparser not installed"]

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    stories, errors = [], []
    skipped_old = 0

    for category, url in build_feed_list(geo):
        parsed, err = fetch_feed(url)
        if err:
            errors.append(f"{category}: {err}")
            continue
        for rank, entry in enumerate(parsed.entries[:max_per_section], 1):
            published = parse_published(entry)
            if published and published < cutoff:
                skipped_old += 1
                continue
            source_name = (entry.get("source") or {}).get("title", "")
            headline, outlet = split_headline(entry.get("title"), source_name)
            if not headline:
                continue
            related = cluster_headlines(entry.get("summary", ""))
            stories.append({
                "headline": headline,
                "outlet": outlet,
                "category": category,
                "rank": rank,
                "published": published,
                "link": entry.get("link", ""),
                "related": related,
            })

    if skipped_old:
        print(f"  Skipped {skipped_old} stories older than {max_age_days}d")
    return stories, errors


def normalize_to_entities(stories, geo, observed_at):
    """Convert raw stories into shared-schema entities.

    The same headline often appears in both the top stories and a topic
    section; it is merged into one entity, keeping the topic's (more
    specific) category and the best rank.
    """
    merged = {}
    for s in stories:
        key = s["headline"].lower().strip()
        if key not in merged:
            merged[key] = dict(s, sections=[s["category"]])
            continue
        m = merged[key]
        m["sections"].append(s["category"])
        if m["category"] == TOP_CATEGORY and s["category"] != TOP_CATEGORY:
            m["category"] = s["category"]
        m["rank"] = min(m["rank"], s["rank"])
        if len(s["related"]) > len(m["related"]):
            m["related"] = s["related"]

    entities = []
    for key, s in merged.items():
        eid = hashlib.sha256(key.encode()).hexdigest()[:16]
        published = s["published"]
        # Related coverage counts the story itself; search feeds carry no
        # cluster, so they read as a single outlet.
        coverage = max(len(s["related"]), 1)
        others = [h for h in s["related"] if not h.lower().startswith(key[:40])]
        context = f"Reported by {s['outlet']}" if s["outlet"] else "Google News story"
        if others:
            context += f"; also: {others[0]}"

        entities.append(make_entity(
            source="google_news",
            entity_type="article_topic",
            entity_id=f"gn_{eid}",
            entity_name=key,
            category=s["category"],
            region=geo,
            observed_at=published.isoformat() if published else observed_at,
            rank=s["rank"],
            primary_value=float(coverage),
            metrics={
                "coverage_count": coverage,
                "sections": sorted(set(s["sections"])),
            },
            raw={
                "title": s["headline"],
                "outlet": s["outlet"],
                "link": s["link"],
                "related_headlines": s["related"][:5],
                "context": context[:300],
                "published_at": published.isoformat() if published else "",
            },
        ))
    return entities


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Google News top stories and topic sections")
    parser.add_argument("--geo", default=DEFAULT_GEO,
        help="Country code: PK, US, IN, GB, AE (default: PK)")
    parser.add_argument("--max-age-days", type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help="Drop stories older than this many days (default: 3)")
    parser.add_argument("--max-per-section", type=int,
        default=DEFAULT_MAX_PER_SECTION,
        help="Top N stories kept from each section (default: 25)")
    args = parser.parse_args()

    geo = (args.geo or "").strip().upper() or FALLBACK_GEO
    if not (args.geo or "").strip():
        print(f"  No geo given (worldwide); Google News has no worldwide "
              f"edition, using {FALLBACK_GEO} as the broadest proxy.")

    feeds = build_feed_list(geo)
    print("Fetching Google News")
    print(f"  Geo     : {geo}"
          + (" (search feeds -- no English edition)" if geo in SEARCH_EDITIONS else ""))
    print(f"  Sections: {len(feeds)}")
    print(f"  Max age : {args.max_age_days} day(s)\n")

    stories, errors = fetch_google_news(geo, args.max_age_days,
                                        args.max_per_section)
    for e in errors:
        print(f"  WARNING: {e}")
    if not stories:
        print("  No stories returned.")
        return

    observed_at = datetime.now(timezone.utc).isoformat()
    entities = normalize_to_entities(stories, geo, observed_at)

    source_meta = {
        "geo": geo,
        "sections_configured": len(feeds),
        "sections_failed": len(errors),
        "total_stories": len(stories),
        "unique_stories": len(entities),
        "fetched_at": observed_at,
    }

    filename = snapshot_filename("snapshot", "google_news")
    write_snapshot(filename, "google_news", source_meta, entities)

    print(f"  Fetched {len(feeds) - len(errors)}/{len(feeds)} sections, "
          f"{len(stories)} stories ({len(entities)} unique)")
    print(f"  Saved {len(entities)} entities -> {filename}\n")

    top = sorted(entities, key=lambda e: e["primary_value"], reverse=True)[:6]
    print("  Most-covered stories:")
    for e in top:
        print(f"    [{e['metrics']['coverage_count']} outlets]  "
              f"{e['raw']['title'][:60]}  ({e['category']})")


if __name__ == "__main__":
    main()
