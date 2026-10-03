"""
wikipedia_trends.py
-------------------
Fetches Wikipedia's most-viewed pages -- what people are looking up.
A different signal from search or news: a person who just went viral,
a film that opened, a match everyone is following.

Source: Wikimedia's public Pageviews API (no key, no auth)
    per country: /metrics/pageviews/top-per-country/<CC>/all-access/Y/M/D
    global:      /metrics/pageviews/top/en.wikipedia/all-access/Y/M/D

LAG AND COVERAGE (checked live):
    - Per-country data is published about 2 days behind, and only for
      countries Wikimedia has loaded -- US, GB and IN work; PK and AE
      return 404. Global English Wikipedia is about 1 day behind.
    - So this tries the country first, walking back a few days to the
      latest published day, and falls back to global English Wikipedia
      when the country has no data. The snapshot records which was used.
    - Because the data is a day or two old, these entries carry their
      real date and age accordingly in trend_scoring.py -- they will not
      be presented as "broke in the last 24 hours".

WHAT WE GET PER PAGE:
    - the page title and its view count for that day (primary_value)
    - Wikipedia's one-line description of the page, fetched in a single
      batched call, as context -- a bare title like "Christa_Pike" tells
      the relevance stage nothing on its own

Output: snapshot_wikipedia_<timestamp>.json (shared schema)

Usage:
    python wikipedia_trends.py
    python wikipedia_trends.py --geo US
"""

import argparse
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from snapshot_schema import make_entity, snapshot_filename, write_snapshot


DEFAULT_GEO = "PK"
TOP_N = 40
MAX_DAYS_BACK = 4
# Wikimedia requires a descriptive User-Agent on API requests.
USER_AGENT = "ScoutAI/1.0 (small-business trend research)"

PER_COUNTRY_URL = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/"
                   "top-per-country/{geo}/all-access/{date}")
GLOBAL_URL = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/"
              "top/en.wikipedia/all-access/{date}")
DESCRIPTIONS_URL = "https://en.wikipedia.org/w/api.php"

# Navigation and maintenance pages, not topics.
SKIP_TITLES = {"Main_Page", "-", "Undefined"}
SKIP_PREFIXES = ("Special:", "Wikipedia:", "Portal:", "File:", "Help:",
                 "Talk:", "Category:", "Template:", "User:", "Draft:")


def _get(url, **kwargs):
    return requests.get(url, timeout=25,
                        headers={"User-Agent": USER_AGENT}, **kwargs)


def is_topic(title):
    return (title not in SKIP_TITLES
            and not title.startswith(SKIP_PREFIXES))


def fetch_latest(url_template, **fmt):
    """Walk back day by day to the most recent published day.

    Returns (day, articles) or (None, []) when nothing is published in
    the last MAX_DAYS_BACK days.
    """
    today = datetime.now(timezone.utc).date()
    for back in range(1, MAX_DAYS_BACK + 1):
        day = today - timedelta(days=back)
        url = url_template.format(date=day.strftime("%Y/%m/%d"), **fmt)
        try:
            resp = _get(url)
        except requests.exceptions.RequestException as e:
            print(f"  WARNING: {e}")
            return None, []
        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            print(f"  WARNING: HTTP {resp.status_code} from {url}")
            return None, []
        items = resp.json().get("items") or []
        if items:
            return day, items[0].get("articles", [])
    return None, []


def fetch_most_viewed(geo):
    """Returns (scope, day, [(title, views, rank)])."""
    articles, day, scope = [], None, None
    if geo:
        day, articles = fetch_latest(PER_COUNTRY_URL, geo=geo)
        if articles:
            scope = geo
            # Per-country lists mix every language edition; keep English
            # so titles are readable and descriptions can be looked up.
            articles = [a for a in articles
                        if a.get("project") == "en.wikipedia"]
        else:
            print(f"  No per-country Wikipedia data for {geo}; "
                  f"falling back to global English Wikipedia.")
    if not articles:
        day, articles = fetch_latest(GLOBAL_URL)
        scope = "global"

    pages = []
    for a in articles:
        title = a.get("article", "")
        if not is_topic(title):
            continue
        views = a.get("views", a.get("views_ceil", 0)) or 0
        pages.append((title, float(views), a.get("rank")))
        if len(pages) >= TOP_N:
            break
    return scope, day, pages


def fetch_descriptions(titles):
    """{title: short description} in one batched call (max 50 titles)."""
    if not titles:
        return {}
    try:
        resp = _get(DESCRIPTIONS_URL, params={
            "action": "query", "prop": "description", "format": "json",
            "formatversion": "2", "redirects": "1",
            "titles": "|".join(t.replace("_", " ") for t in titles[:50]),
        })
        resp.raise_for_status()
        query = resp.json().get("query", {})
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"  WARNING: could not fetch page descriptions: {e}")
        return {}

    # Map redirect/normalized titles back to what we asked for.
    alias = {}
    for step in query.get("normalized", []) + query.get("redirects", []):
        alias[step["to"]] = alias.get(step["from"], step["from"])
    out = {}
    for page in query.get("pages", []):
        desc = page.get("description")
        if desc:
            asked = alias.get(page["title"], page["title"])
            out[asked.replace(" ", "_")] = desc
    return out


def normalize_to_entities(pages, descriptions, scope, day, region):
    # The day's data covers the whole UTC day; dating it at the end of the
    # day gives the most generous (smallest) honest age.
    seen_at = datetime(day.year, day.month, day.day, 23, 59, 59,
                       tzinfo=timezone.utc).isoformat()
    where = "worldwide (English Wikipedia)" if scope == "global" else f"in {scope}"
    entities = []
    for title, views, rank in pages:
        name = title.replace("_", " ")
        desc = descriptions.get(title, "")
        context = f"Wikipedia page, ~{int(views):,} views {where} on {day.isoformat()}"
        if desc:
            context = f"{desc}. {context}"
        entities.append(make_entity(
            source="wikipedia",
            entity_type="page",
            entity_id=f"wp_{title.lower()[:60]}",
            entity_name=name.lower(),
            category="General",
            region=region,
            observed_at=seen_at,
            rank=rank,
            primary_value=views,
            metrics={"views": views, "scope": scope, "day": day.isoformat()},
            raw={
                "title": name,
                "description": desc,
                "url": f"https://en.wikipedia.org/wiki/{quote(title)}",
                "context": context[:300],
                "published_at": seen_at,
            },
        ))
    return entities


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Wikipedia's most-viewed pages")
    parser.add_argument("--geo", default=DEFAULT_GEO,
        help="Country code: PK, US, IN, GB, AE; empty for worldwide "
             "(default: PK)")
    args = parser.parse_args()

    geo = (args.geo or "").strip().upper()
    print("Fetching Wikipedia most-viewed pages")
    print(f"  Geo: {geo or 'worldwide'}\n")

    scope, day, pages = fetch_most_viewed(geo)
    if not pages:
        print("  No pageview data returned.")
        return

    descriptions = fetch_descriptions([t for t, _, _ in pages])
    entities = normalize_to_entities(pages, descriptions, scope, day, geo)

    observed_at = datetime.now(timezone.utc).isoformat()
    source_meta = {
        "geo": geo,
        "scope": scope,
        "data_day": day.isoformat(),
        "fetched_at": observed_at,
    }

    filename = snapshot_filename("snapshot", "wikipedia")
    write_snapshot(filename, "wikipedia", source_meta, entities)

    print(f"  Data for {day.isoformat()} ({scope}), "
          f"{len(descriptions)}/{len(entities)} with descriptions")
    print(f"  Saved {len(entities)} entities -> {filename}\n")
    print("  Most viewed:")
    for e in entities[:8]:
        print(f"    {int(e['primary_value']):>10,}  {e['raw']['title'][:40]:40} "
              f"{e['raw']['description'][:40]}")


if __name__ == "__main__":
    main()
