"""
reddit_trends.py
----------------
Fetches what people are actually talking about on Reddit: the hot posts
in a country's own subreddits, plus the day's top posts in subreddits
that match the kinds of businesses using this tool (food, beauty,
fashion, fitness, small business, entertainment).

Source: Reddit's public RSS feeds, e.g.
    https://www.reddit.com/r/pakistan+karachi+lahore/hot/.rss
    No API key, no OAuth.

WHY RSS AND NOT THE JSON API:
    Unauthenticated requests to Reddit's .json endpoints now get HTTP 403.
    The RSS feeds are still served. They carry no upvote counts, so a
    post's position in the listing stands in as its signal (see
    primary_value below).

RATE LIMIT:
    Unauthenticated RSS is limited to roughly one request a minute per IP
    (a second request seconds later gets HTTP 429). So every subreddit for
    a listing goes into ONE multi-subreddit URL ("r/a+b+c"), there are only
    two requests per run, and a 429 is retried once after the reset time
    Reddit reports.

WHAT WE GET PER POST:
    - the title, subreddit and post time
    - primary_value = listing position strength (LIMIT + 1 - rank), so a
      post climbing from #40 to #5 between runs shows up as positive
      velocity in trend_scoring.py
    - the first lines of the post body as context

Output: snapshot_reddit_<timestamp>.json (shared schema)

Usage:
    python reddit_trends.py
    python reddit_trends.py --geo GB
    python reddit_trends.py --geo PK --skip-topical
"""

import re
import html
import time
import argparse
import requests
from datetime import datetime, timedelta, timezone
from snapshot_schema import make_entity, snapshot_filename, write_snapshot

try:
    import feedparser
except ImportError:
    feedparser = None


DEFAULT_GEO = "PK"
LIMIT = 50
DEFAULT_MAX_AGE_DAYS = 3
# Reddit asks API clients for a descriptive User-Agent.
USER_AGENT = "ScoutAI/1.0 (small-business trend research)"
MAX_RETRY_WAIT_SECONDS = 70

LISTING_URL = "https://www.reddit.com/r/{subs}/{sort}/.rss?limit={limit}{extra}"

# Country subreddits, read by "hot". Empty geo (worldwide) -> r/popular.
REGIONAL_SUBS = {
    "PK": ["pakistan", "karachi", "lahore", "islamabad"],
    "AE": ["dubai", "UAE", "abudhabi"],
    "IN": ["india", "mumbai", "delhi", "bangalore"],
    "GB": ["unitedkingdom", "CasualUK", "london"],
    "US": ["news", "nyc", "LosAngeles", "chicago"],
}
WORLDWIDE_SUBS = ["popular"]

# Topical subreddits, read by the day's top posts, with the project
# category each one maps to (see snapshot_schema.CATEGORIES).
TOPICAL_SUBS = {
    "food":                "Food & Beverage",
    "Cooking":             "Food & Beverage",
    "Baking":              "Food & Beverage",
    "SkincareAddiction":   "Fashion & Beauty",
    "MakeupAddiction":     "Fashion & Beauty",
    "femalefashionadvice": "Fashion & Beauty",
    "malefashionadvice":   "Fashion & Beauty",
    "Fitness":             "Health & Wellness",
    "smallbusiness":       "Business & Finance",
    "Entrepreneur":        "Business & Finance",
    "gadgets":             "Technology",
    "movies":              "Entertainment",
    "television":          "Entertainment",
}
REGIONAL_CATEGORY = "News & Politics"

# Recurring housekeeping posts that sit at the top of a subreddit without
# being a trend.
HOUSEKEEPING = re.compile(
    r"\b(megathread|daily (discussion|thread)|weekly (discussion|thread)|"
    r"discussion thread|random discussion|monthly thread|rules|"
    r"moderator|mod post|ama request)\b", re.IGNORECASE)


def listing_url(subs, sort, extra=""):
    return LISTING_URL.format(subs="+".join(subs), sort=sort,
                              limit=LIMIT, extra=extra)


def fetch_listing(url):
    """GET one RSS listing, retrying once on 429. Returns (feed, error)."""
    for attempt in (1, 2):
        try:
            resp = requests.get(url, timeout=25,
                                headers={"User-Agent": USER_AGENT})
        except requests.exceptions.RequestException as e:
            return None, str(e)
        if resp.status_code == 429 and attempt == 1:
            try:
                wait = float(resp.headers.get("x-ratelimit-reset", 60))
            except ValueError:
                wait = 60.0
            wait = min(max(wait, 5.0), MAX_RETRY_WAIT_SECONDS) + 2
            print(f"  Rate limited by Reddit; retrying in {wait:.0f}s")
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        parsed = feedparser.parse(resp.content)
        if not parsed.entries:
            return None, "listing returned no posts"
        return parsed, None
    return None, "HTTP 429 (still rate limited after retry)"


def parse_time(entry):
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def post_text(entry):
    """Plain-text start of the post body, without Reddit's footer links."""
    content = ""
    if entry.get("content"):
        content = entry["content"][0].get("value", "")
    content = content or entry.get("summary", "")
    content = re.sub(r"submitted by.*$", "", content, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:220]


def collect_posts(feed, listing, category_for, max_age_days):
    """Posts from one listing. "regional" posts belong to the fetched
    country; "topical" ones come from global subreddits (r/food is mostly
    American) and are labelled as such downstream, so a global post is
    never presented as a trend in the selected country."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    posts = []
    for rank, entry in enumerate(feed.entries, 1):
        title = (entry.get("title") or "").strip()
        if not title or HOUSEKEEPING.search(title):
            continue
        posted = parse_time(entry)
        if posted and posted < cutoff:
            continue
        tags = entry.get("tags") or []
        sub = tags[0].get("term", "") if tags else ""
        posts.append({
            "title": title,
            "subreddit": sub,
            "category": category_for(sub),
            "listing": listing,
            "rank": rank,
            "posted": posted,
            "link": entry.get("link", ""),
            "id": (entry.get("id") or entry.get("link") or title),
            "text": post_text(entry),
        })
    return posts


def fetch_reddit(geo, include_topical=True, max_age_days=DEFAULT_MAX_AGE_DAYS):
    """Returns (posts, errors)."""
    if feedparser is None:
        print("  ERROR: feedparser is not installed.")
        print("  Install with: pip install feedparser")
        return [], ["feedparser not installed"]

    posts, errors = [], []

    regional = REGIONAL_SUBS.get(geo, WORLDWIDE_SUBS)
    feed, err = fetch_listing(listing_url(regional, "hot"))
    if err:
        errors.append(f"regional (r/{'+'.join(regional)}): {err}")
    else:
        posts += collect_posts(feed, "regional",
                               lambda sub: REGIONAL_CATEGORY, max_age_days)

    if include_topical:
        topical = list(TOPICAL_SUBS)
        lookup = {k.lower(): v for k, v in TOPICAL_SUBS.items()}
        feed, err = fetch_listing(listing_url(topical, "top", "&t=day"))
        if err:
            errors.append(f"topical: {err}")
        else:
            posts += collect_posts(
                feed, "topical",
                lambda sub: lookup.get(sub.lower(), "General"), max_age_days)

    return posts, errors


def normalize_to_entities(posts, geo, observed_at):
    entities = []
    seen = set()
    for p in posts:
        post_id = re.sub(r"[^A-Za-z0-9_]", "", p["id"].split("/")[-1])[:20] \
            or str(abs(hash(p["title"])))
        if post_id in seen:
            continue
        seen.add(post_id)
        posted = p["posted"]
        sub_label = f"r/{p['subreddit']}" if p["subreddit"] else "Reddit"
        is_global = p["listing"] == "topical"
        context = f"{sub_label} post" + (" (global subreddit)" if is_global else "")
        if p["text"]:
            context += f": {p['text']}"

        entities.append(make_entity(
            source="reddit",
            entity_type="post",
            entity_id=f"rd_{post_id}",
            entity_name=p["title"].lower(),
            category=p["category"],
            region="global" if is_global else geo,
            observed_at=posted.isoformat() if posted else observed_at,
            rank=p["rank"],
            primary_value=float(LIMIT + 1 - p["rank"]),
            metrics={
                "listing": p["listing"],
                "listing_rank": p["rank"],
                "subreddit": p["subreddit"],
            },
            raw={
                "title": p["title"],
                "subreddit": p["subreddit"],
                "link": p["link"],
                "context": context[:300],
                "published_at": posted.isoformat() if posted else "",
            },
        ))
    return entities


def main():
    parser = argparse.ArgumentParser(
        description="Fetch hot and top Reddit posts")
    parser.add_argument("--geo", default=DEFAULT_GEO,
        help="Country code: PK, US, IN, GB, AE; empty for worldwide "
             "(default: PK)")
    parser.add_argument("--skip-topical", action="store_true",
        help="Only fetch the country's subreddits (one request instead "
             "of two)")
    parser.add_argument("--max-age-days", type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help="Drop posts older than this many days (default: 3)")
    args = parser.parse_args()

    geo = (args.geo or "").strip().upper()
    regional = REGIONAL_SUBS.get(geo, WORLDWIDE_SUBS)

    print("Fetching Reddit")
    print(f"  Region   : {geo or 'worldwide'} (r/{'+'.join(regional)})")
    print(f"  Topical  : {'skipped' if args.skip_topical else f'{len(TOPICAL_SUBS)} subreddits'}")
    print(f"  Max age  : {args.max_age_days} day(s)\n")

    posts, errors = fetch_reddit(geo, not args.skip_topical,
                                 args.max_age_days)
    for e in errors:
        print(f"  WARNING: {e}")
    if not posts:
        print("  No posts returned.")
        return

    observed_at = datetime.now(timezone.utc).isoformat()
    entities = normalize_to_entities(posts, geo, observed_at)

    source_meta = {
        "geo": geo,
        "regional_subreddits": regional,
        "topical_subreddits": [] if args.skip_topical else list(TOPICAL_SUBS),
        "errors": errors,
        "fetched_at": observed_at,
    }

    filename = snapshot_filename("snapshot", "reddit")
    write_snapshot(filename, "reddit", source_meta, entities)
    print(f"  Saved {len(entities)} entities -> {filename}\n")

    for listing in ("regional", "topical"):
        top = [e for e in entities if e["metrics"]["listing"] == listing][:4]
        if top:
            print(f"  Top {listing} posts:")
            for e in top:
                print(f"    r/{e['raw']['subreddit']:<18} {e['raw']['title'][:60]}")


if __name__ == "__main__":
    main()
