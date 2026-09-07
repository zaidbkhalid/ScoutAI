"""
google_trends.py
----------------
Fetches broad, general trending keywords for a given country.

Output: snapshot_<timestamp>.json in the shared schema, containing
normalized entities from keyword interest + related queries.

Usage:
    python google_trends.py
    python google_trends.py --keywords "PSL,rain Karachi,Eid" --geo PK
"""

import json
import time
import argparse
from datetime import datetime, timezone
from pytrends.request import TrendReq
from snapshot_schema import make_entity, snapshot_filename, write_snapshot


# -- Defaults (used when run standalone, overridden via CLI or app.py) --
DEFAULT_KEYWORDS = ["PSL", "rain Karachi", "Eid", "drama Pakistan", "celebrity"]
DEFAULT_GEO      = "PK"
DEFAULT_TIMEFRAME = "now 7-d"
# -----------------------------------------------------------------------

BREAKOUT_VALUE = 5000  # Google labels brand-new queries "Breakout"


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


def _parse_value(raw):
    """Parse a numeric value; treat 'Breakout' as BREAKOUT_VALUE."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        if raw.lower() == "breakout":
            return float(BREAKOUT_VALUE)
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def fetch_google_trends(keywords, timeframe, geo):
    time.sleep(3)  # polite buffer before first request
    pytrends = TrendReq(hl="en-US", tz=300, timeout=(10, 25))
    pytrends.build_payload(keywords, timeframe=timeframe, geo=geo)

    print("  Fetching interest over time...")
    iot_df = request_with_retry(
        pytrends.interest_over_time, "interest_over_time")
    if not iot_df.empty:
        iot_df = iot_df.drop(columns=["isPartial"], errors="ignore")
        interest_over_time = [
            {"datetime": str(ts),
             **{kw: int(row[kw]) for kw in keywords if kw in row}}
            for ts, row in iot_df.iterrows()
        ]
    else:
        interest_over_time = []

    time.sleep(5)

    print("  Fetching related queries...")
    related_raw = request_with_retry(
        pytrends.related_queries, "related_queries")
    related_queries = {}
    for kw in keywords:
        entry = {}
        if kw in related_raw:
            for kind in ("rising", "top"):
                df = related_raw[kw].get(kind)
                if df is not None and not df.empty:
                    entry[kind] = df.head(10).to_dict(orient="records")
        related_queries[kw] = entry

    time.sleep(5)

    print("  Fetching related topics...")
    related_topics_raw = request_with_retry(
        pytrends.related_topics, "related_topics")
    related_topics = {}
    for kw in keywords:
        entry = {}
        if kw in related_topics_raw:
            for kind in ("rising", "top"):
                df = related_topics_raw[kw].get(kind)
                if df is not None and not df.empty:
                    cols = [c for c in ["topic_title", "topic_type", "value"]
                            if c in df.columns]
                    entry[kind] = df[cols].head(10).to_dict(orient="records")
        related_topics[kw] = entry

    return {
        "meta": {
            "keywords"  : keywords,
            "timeframe" : timeframe,
            "geo"       : geo,
            "fetched_at": datetime.now(timezone.utc).isoformat()
        },
        "interest_over_time": interest_over_time,
        "related_queries"   : related_queries,
        "related_topics"    : related_topics,
    }


def normalize_to_entities(data):
    """Convert raw google_trends data into shared-schema entities."""
    entities = []
    keywords = data.get("meta", {}).get("keywords", [])
    geo = data.get("meta", {}).get("geo", "")
    observed_at = data.get("meta", {}).get("fetched_at", "")

    # 1. Keyword average interest
    iot = data.get("interest_over_time", [])
    if iot and keywords:
        for kw in keywords:
            values = [point.get(kw) for point in iot if kw in point]
            values = [v for v in values if v is not None]
            if values:
                avg_interest = sum(values) / len(values)
                latest_interest = values[-1]
                entities.append(make_entity(
                    source="google_trends",
                    entity_type="keyword",
                    entity_id=f"gt_kw_{kw.lower().replace(' ', '_')}",
                    entity_name=kw.lower().strip(),
                    category="General",
                    region=geo,
                    observed_at=observed_at,
                    primary_value=float(latest_interest),
                    metrics={
                        "avg_interest": round(avg_interest, 1),
                        "latest_interest": latest_interest,
                        "num_datapoints": len(values),
                    },
                    raw={"keyword": kw},
                ))

    # 2. Related queries (rising and top)
    related = data.get("related_queries", {})
    for kw, queries in related.items():
        for kind in ("rising", "top"):
            for rank_idx, item in enumerate(queries.get(kind, []), 1):
                val = _parse_value(item.get("value"))
                if val is None:
                    continue
                query_text = item.get("query", "").lower().strip()
                if not query_text:
                    continue
                entities.append(make_entity(
                    source="google_trends",
                    entity_type="related_query",
                    entity_id=f"gt_rq_{query_text.replace(' ', '_')}",
                    entity_name=query_text,
                    category="General",
                    region=geo,
                    observed_at=observed_at,
                    rank=rank_idx,
                    primary_value=val,
                    metrics={"value": val, "query_kind": kind},
                    raw={"query": item.get("query"),
                         "value": item.get("value"),
                         "kind": kind, "parent_keyword": kw},
                ))

    return entities


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords", default=None,
        help="Comma-separated keywords, max 5. e.g. 'PSL,rain Karachi,Eid'")
    parser.add_argument("--geo", default=DEFAULT_GEO,
        help="Country code. e.g. PK, US, IN (default: PK)")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME,
        help="Timeframe. e.g. 'now 7-d', 'today 1-m' (default: now 7-d)")
    args = parser.parse_args()

    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",")][:5]
    else:
        keywords = DEFAULT_KEYWORDS

    print(f"Fetching Google Trends")
    print(f"  Keywords : {keywords}")
    print(f"  Geo      : {args.geo}")
    print(f"  Timeframe: {args.timeframe}\n")

    data = fetch_google_trends(keywords, args.timeframe, args.geo)
    entities = normalize_to_entities(data)

    filename = snapshot_filename("snapshot", "google_trends")
    write_snapshot(filename, "google_trends", data["meta"], entities)

    print(f"\n  Saved {len(entities)} entities -> {filename}")


if __name__ == "__main__":
    main()
