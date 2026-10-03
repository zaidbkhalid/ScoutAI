"""
test_fetchers.py
----------------
Offline tests for the parsing/normalization in the trend fetchers
(google_news.py, reddit_trends.py, wikipedia_trends.py) and for the
brand-safety filter they all feed into. No network access needed --
every test builds its input by hand.

Usage:
    python test_fetchers.py
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))

import google_news
import reddit_trends
import wikipedia_trends
from brand_safety import is_brand_safe


NOW = datetime.now(timezone.utc).replace(microsecond=0)


# ---- google_news ----

def test_google_news_split_headline():
    assert google_news.split_headline("Petrol price cut - Dawn", "Dawn") == \
        ("Petrol price cut", "Dawn")
    # Outlet missing from <source>: taken from the title's tail.
    assert google_news.split_headline("Eid sale opens - Express Tribune", "") == \
        ("Eid sale opens", "Express Tribune")
    # Hyphens inside the headline survive; only the last " - " splits.
    assert google_news.split_headline("Low-cost cafe - chain grows - Reuters", "Reuters") == \
        ("Low-cost cafe - chain grows", "Reuters")
    print("  PASS google_news_split_headline")


def test_google_news_cluster_headlines():
    summary = (
        '<ol><li><a href="x" target="_blank">First &amp; best story</a>'
        '&nbsp;&nbsp;<font color="#6f6f6f">NPR</font></li>'
        '<li><a href="y">Second story</a>&nbsp;&nbsp;<font>Dawn</font></li></ol>')
    assert google_news.cluster_headlines(summary) == \
        ["First & best story", "Second story"], "outlet names must not leak in"
    assert google_news.cluster_headlines("") == []
    print("  PASS google_news_cluster_headlines")


def test_google_news_merge_and_coverage():
    story = {"headline": "Biscoff latte launches", "outlet": "Dawn",
             "published": NOW - timedelta(hours=2), "link": "l"}
    stories = [
        dict(story, category=google_news.TOP_CATEGORY, rank=4, related=["a", "b", "c"]),
        dict(story, category="Business & Finance", rank=2, related=["a"]),
        {"headline": "Solo story", "outlet": "", "category": "Sports",
         "rank": 1, "published": None, "link": "", "related": []},
    ]
    ents = {e["entity_name"]: e for e in
            google_news.normalize_to_entities(stories, "PK", NOW.isoformat())}
    assert len(ents) == 2, "same headline in two sections must merge"
    merged = ents["biscoff latte launches"]
    assert merged["category"] == "Business & Finance", "topic beats top stories"
    assert merged["rank"] == 2
    assert merged["primary_value"] == 3.0, "coverage = largest cluster"
    assert merged["region"] == "PK"
    assert ents["solo story"]["primary_value"] == 1.0, "no cluster -> 1 outlet"
    print("  PASS google_news_merge_and_coverage")


def test_google_news_uae_uses_search():
    feeds = google_news.build_feed_list("AE")
    assert feeds and all("/rss/search?" in url for _, url in feeds)
    pk = google_news.build_feed_list("PK")
    assert len(pk) == 1 + len(google_news.TOPICS)
    assert all("gl=PK" in url for _, url in pk)
    print("  PASS google_news_uae_uses_search")


# ---- reddit_trends ----

class _Feed:
    def __init__(self, entries):
        self.entries = entries


def _post(title, sub, hours_ago=1, pid=None):
    return {
        "title": title,
        "tags": [{"term": sub}],
        "published": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "link": f"https://www.reddit.com/r/{sub}/comments/{pid or title[:6]}/",
        "id": f"t3_{pid or abs(hash(title))}",
        "content": [{"value": "<p>Body text here</p> submitted by /u/x [link]"}],
    }


def test_reddit_collect_filters_and_ranks():
    feed = _Feed([
        _post("Daily Discussion Thread - October 02", "pakistan", pid="a1"),
        _post("Best nihari in Karachi?", "karachi", pid="a2"),
        _post("Old post", "lahore", hours_ago=24 * 5, pid="a3"),
        _post("New flats in Blue Area", "islamabad", pid="a4"),
    ])
    posts = reddit_trends.collect_posts(feed, "regional", lambda s: "News & Politics", 3)
    titles = [p["title"] for p in posts]
    assert titles == ["Best nihari in Karachi?", "New flats in Blue Area"], titles
    assert posts[0]["text"] == "Body text here", "footer must be stripped"

    ents = reddit_trends.normalize_to_entities(posts, "PK", NOW.isoformat())
    # Listing position, not raw rank: higher is stronger.
    assert ents[0]["primary_value"] == reddit_trends.LIMIT + 1 - 2
    assert ents[0]["primary_value"] > ents[1]["primary_value"]
    assert ents[0]["raw"]["context"].startswith("r/karachi post")
    print("  PASS reddit_collect_filters_and_ranks")


def test_reddit_dedupes_crossposted_listing():
    feed = _Feed([_post("Same post", "food", pid="z9"), _post("Same post", "food", pid="z9")])
    posts = reddit_trends.collect_posts(feed, "topical", lambda s: "Food & Beverage", 3)
    assert len(reddit_trends.normalize_to_entities(posts, "PK", NOW.isoformat())) == 1
    print("  PASS reddit_dedupes_crossposted_listing")


# ---- wikipedia_trends ----

def test_wikipedia_is_topic():
    for skip in ("Main_Page", "Special:Search", "Wikipedia:Featured_pictures", "-"):
        assert not wikipedia_trends.is_topic(skip), skip
    assert wikipedia_trends.is_topic("2026_Asian_Games")
    print("  PASS wikipedia_is_topic")


def test_wikipedia_entities():
    day = date.today() - timedelta(days=2)
    pages = [("2026_Asian_Games", 209287.0, 7), ("Bella_Ramsey", 198968.0, 8)]
    desc = {"2026_Asian_Games": "Multi-sport event in Aichi and Nagoya, Japan"}
    ents = wikipedia_trends.normalize_to_entities(pages, desc, "global", day, "PK")
    games, bella = ents
    assert games["entity_name"] == "2026 asian games"
    assert games["raw"]["context"].startswith("Multi-sport event")
    assert "worldwide" in games["raw"]["context"], "global fallback must say so"
    assert bella["raw"]["description"] == ""
    assert games["observed_at"].startswith(day.isoformat()), "dated by data day"
    assert games["primary_value"] == 209287.0
    print("  PASS wikipedia_entities")


# ---- brand_safety additions ----

def test_brand_safety_new_patterns():
    unsafe = [
        "2 civilians martyred in firing by indian forces",
        "American murderer (born 1976)",
        "Alleged sex crime in Ithaca, New York",
        "inmate on death row",
        "leaked onlyfans content",
    ]
    safe = [
        "firing up the grill for eid",
        "killer playlist for the weekend",
        "Belgian Chocolate Truffle",
        "2026 Asian Games medal table",
        "pumpkin costume teddybear cookies",
    ]
    for t in unsafe:
        assert not is_brand_safe(t), f"should be filtered: {t}"
    for t in safe:
        assert is_brand_safe(t), f"should pass: {t}"
    print("  PASS brand_safety_new_patterns")


def test_reddit_topical_posts_are_global():
    """A post from a global topical subreddit (r/food) must not be
    labelled as a trend in the selected country."""
    feed = _Feed([_post("[homemade] pork chile verde", "food", pid="pc1")])
    posts = reddit_trends.collect_posts(feed, "topical", lambda s: "Food & Beverage", 3)
    ent = reddit_trends.normalize_to_entities(posts, "PK", NOW.isoformat())[0]
    assert ent["region"] == "global", ent["region"]
    assert "global subreddit" in ent["raw"]["context"]
    print("  PASS reddit_topical_posts_are_global")


def test_brand_safety_regional():
    """PK/AE add pork, alcohol and gambling filters; other regions don't,
    and look-alike words survive."""
    for region in ("PK", "AE"):
        for t in ["[homemade] pork chile verde", "best cocktails in dubai",
                  "craft beer festival", "online casino bonus"]:
            assert not is_brand_safe(t, region=region), (region, t)
    assert is_brand_safe("[homemade] pork chile verde", region="US")
    assert is_brand_safe("[homemade] pork chile verde")
    for t in ["ginger chai recipe", "hamburger and shami kebab",
              "rumi poetry night", "pistachio kunafa"]:
        assert is_brand_safe(t, region="PK"), t
    print("  PASS brand_safety_regional")


if __name__ == "__main__":
    print("\nFetcher Tests (offline)")
    print("-" * 40)

    tests = [
        test_google_news_split_headline,
        test_google_news_cluster_headlines,
        test_google_news_merge_and_coverage,
        test_google_news_uae_uses_search,
        test_reddit_collect_filters_and_ranks,
        test_reddit_dedupes_crossposted_listing,
        test_wikipedia_is_topic,
        test_wikipedia_entities,
        test_brand_safety_new_patterns,
        test_reddit_topical_posts_are_global,
        test_brand_safety_regional,
    ]

    passed = failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL {test_fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n  Results: {passed} passed, {failed} failed "
          f"out of {len(tests)}")
    if failed:
        sys.exit(1)
    else:
        print("  All tests passed!\n")
