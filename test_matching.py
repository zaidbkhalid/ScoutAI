"""
test_matching.py
----------------
Offline tests for the guard rails around business matching
(trend_analyzer.py) and the copy checks (ad_copy.py, copy_playbook.py).
No API key or network needed.

Usage:
    python test_matching.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))

import trend_analyzer as ta
import ad_copy
from copy_playbook import find_invented_offers, find_banned


def t(name, source="google_news", category="News & Politics", region="PK"):
    return {"entity_name": name, "source": source, "category": category, "region": region}


def test_pool_excludes_global_reddit_and_caps_local():
    scores = ([t(f"global post {i}", "reddit", "Food & Beverage", "global") for i in range(10)]
              + [t(f"karachi post {i}", "reddit", "News & Politics", "PK") for i in range(10)]
              + [t(f"story {i}") for i in range(30)])
    pool = ta.build_relevance_pool(scores, 45)
    names = [p["entity_name"] for p in pool]
    assert not any(n.startswith("global post") for n in names), "global topical Reddit must not be matched"
    assert sum(n.startswith("karachi post") for n in names) <= ta.MAX_REDDIT_IN_POOL
    print("  PASS pool_excludes_global_reddit_and_caps_local")


def test_enforce_match_rules():
    pool = [t("ind vs wi", "google_trending_now"), t("post a", "reddit"), t("post b", "reddit")]
    matches = [
        {"trend": "post a", "relevance_score": 8},
        {"trend": "post b", "relevance_score": 9},
        {"trend": "ind vs wi", "relevance_score": 7},
        {"trend": "made up trend", "relevance_score": 10},
    ]
    kept = [m["trend"] for m in ta.enforce_match_rules(matches, pool)]
    assert "made up trend" not in kept, "trends not in the pool must be dropped"
    assert kept == ["post b", "ind vs wi"], kept   # best Reddit post only, ranked by score
    print("  PASS enforce_match_rules")


def test_copy_limits_and_phrases():
    draft = {"formats": [
        {"format": "google_search_ad",
         "variants": ["Short one", "Also fine here", "This headline is far too long for Google"],
         "descriptions": ["x" * 95]},
        {"format": "instagram_post", "primary_text": "Hey there! Elevate your weekend."},
    ]}
    problems = ad_copy.find_problems(draft)
    assert any("headline 3 is 40 chars" in p for p in problems), problems
    assert any("description 1 is 95 chars" in p for p in problems), problems
    assert any('"elevate"' in p for p in problems) and any('"hey there"' in p for p in problems)
    ad_copy.enforce_limits(draft)
    assert draft["formats"][0]["variants"] == ["Short one", "Also fine here"], "over-limit headline dropped"
    assert "Check:" in draft["formats"][0].get("notes", ""), "remaining over-limit field flagged"
    print("  PASS copy_limits_and_phrases")


def test_invented_offers():
    profile = '{"products": "Free tier plus PKR 1,500/month plan"}'
    assert "free for" in find_invented_offers("Try Khaata free for one month!", profile)
    assert find_invented_offers("Grab yours with a discount, 20% off", "{}") == ["20% off", "discount"]
    assert find_invented_offers("Get 20% off", "we always run 20% off on Fridays") == []
    assert find_invented_offers("Our free tier is enough for most shops", profile) == []
    print("  PASS invented_offers")


def test_banned_phrases_whole_words():
    assert find_banned("Elevated platforms") == [], "only whole phrases count"
    assert find_banned("Unlock the secret") == ["unlock"]
    print("  PASS banned_phrases_whole_words")


if __name__ == "__main__":
    print("\nMatching + copy guard-rail tests (offline)")
    print("-" * 40)
    tests = [test_pool_excludes_global_reddit_and_caps_local, test_enforce_match_rules,
             test_copy_limits_and_phrases, test_invented_offers, test_banned_phrases_whole_words]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n  Results: {len(tests) - failed} passed, {failed} failed out of {len(tests)}")
    sys.exit(1 if failed else 0)
