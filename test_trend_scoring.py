"""
test_trend_scoring.py
---------------------
Standalone test for trend_scoring.py and the shared snapshot schema.

Constructs synthetic snapshot JSON files from multiple sources using the
shared schema, runs the scoring engine, and verifies correctness.

Usage:
    python test_trend_scoring.py
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
from trend_scoring import (
    parse_timestamp_from_filename,
    load_all_snapshots,
    group_observations,
    compute_velocity,
    compute_score,
    score_all_trends,
    select_top_trends,
    NEW_ENTITY_SCORE,
)
from snapshot_schema import make_entity, write_snapshot, SCHEMA_VERSION


# ---- helpers ----

def make_google_trends_snapshot(keyword, interest_values, ts, geo="PK"):
    """Build entities for a google_trends keyword snapshot."""
    entities = []
    latest = interest_values[-1] if interest_values else 0
    avg = sum(interest_values) / max(len(interest_values), 1)
    entities.append(make_entity(
        source="google_trends",
        entity_type="keyword",
        entity_id=f"gt_kw_{keyword.lower().replace(' ', '_')}",
        entity_name=keyword.lower().strip(),
        category="General",
        region=geo,
        observed_at=ts.isoformat(),
        primary_value=float(latest),
        metrics={"avg_interest": round(avg, 1),
                 "latest_interest": latest,
                 "num_datapoints": len(interest_values)},
        raw={"keyword": keyword},
    ))
    return entities


def make_youtube_snapshot(videos, ts, geo="PK"):
    """Build entities for a youtube_trending snapshot.
    videos: list of (title, view_count) tuples.
    """
    entities = []
    for rank, (title, views) in enumerate(videos, 1):
        entities.append(make_entity(
            source="youtube_trending",
            entity_type="video",
            entity_id=f"yt_{title.lower().replace(' ', '_')[:20]}",
            entity_name=title.lower().strip(),
            category="Entertainment",
            region=geo,
            observed_at=ts.isoformat(),
            rank=rank,
            primary_value=float(views),
            metrics={"view_count": views, "like_count": views // 50,
                     "comment_count": views // 500},
            raw={"title": title, "channel": "TestChannel"},
        ))
    return entities


def make_rss_snapshot(articles, ts):
    """Build entities for an rss snapshot.
    articles: list of (title, category) tuples.
    """
    import hashlib
    entities = []
    for title, category in articles:
        eid = hashlib.sha256(title.lower().encode()).hexdigest()[:16]
        entities.append(make_entity(
            source="rss",
            entity_type="article_topic",
            entity_id=f"rss_{eid}",
            entity_name=title.lower().strip(),
            category=category,
            region="global",
            observed_at=ts.isoformat(),
            primary_value=1.0,
            metrics={"feed_count": 1},
            raw={"title": title, "sources": ["TestFeed"]},
        ))
    return entities


# ---- unit tests ----

def test_parse_timestamp():
    """Filename timestamp extraction returns UTC-aware datetime."""
    ts = parse_timestamp_from_filename("snapshot_20260402_170603.json")
    assert ts is not None
    assert ts.year == 2026 and ts.month == 4 and ts.day == 2
    assert ts.hour == 17 and ts.minute == 6 and ts.second == 3
    assert ts.tzinfo is not None, "Must be timezone-aware"
    assert ts.tzinfo == timezone.utc, "Must be UTC"
    assert parse_timestamp_from_filename("random_file.json") is None
    print("  PASS parse_timestamp_from_filename")


def test_compute_velocity():
    """Velocity computation with known values."""
    now = datetime.now(timezone.utc)
    obs = [
        {"primary_value": 10.0, "timestamp": now - timedelta(hours=10)},
        {"primary_value": 30.0, "timestamp": now},
    ]
    vel = compute_velocity(obs)
    assert vel is not None
    assert abs(vel - 0.2) < 0.001, f"Expected ~0.2, got {vel}"

    assert compute_velocity(
        [{"primary_value": 5.0, "timestamp": now}]) is None

    obs_decline = [
        {"primary_value": 50.0, "timestamp": now - timedelta(hours=10)},
        {"primary_value": 5.0, "timestamp": now},
    ]
    vel_dec = compute_velocity(obs_decline)
    assert vel_dec is not None
    assert vel_dec < 0, f"Expected negative velocity, got {vel_dec}"
    print("  PASS compute_velocity")


def test_compute_score():
    """Score: velocity + age decay."""
    score_young = compute_score(velocity=0.5, age_hours=2.0, is_new=False)
    score_old = compute_score(velocity=0.5, age_hours=200.0, is_new=False)
    assert score_young > score_old

    score_new = compute_score(velocity=None, age_hours=1.0, is_new=True)
    assert 0 < score_new <= NEW_ENTITY_SCORE

    strong_rising = compute_score(velocity=1.0, age_hours=1.0, is_new=False)
    assert strong_rising > score_new
    print("  PASS compute_score")


def test_end_to_end_single_source():
    """Full pipeline with synthetic google_trends snapshots."""
    tmpdir = tempfile.mkdtemp(prefix="trend_test_")
    try:
        # Relative to now, not a fixed date: scores decay with age, so a
        # hardcoded date makes this test start failing once it ages out.
        base_time = (datetime.now(timezone.utc) - timedelta(hours=73)).replace(microsecond=0)

        for i in range(4):
            ts = base_time + timedelta(hours=24 * i)
            ts_str = ts.strftime("%Y%m%d_%H%M%S")

            # rocket_trend: 10 -> 20 -> 40 -> 80
            rocket_vals = [10, 20, 40, 80][:i + 1]
            rocket_vals += [rocket_vals[-1]] * max(0, 3 - len(rocket_vals))

            # flat_trend: always 50
            flat_vals = [50] * max(2, i + 1)

            entities = []
            entities += make_google_trends_snapshot(
                "rocket_trend", rocket_vals, ts)
            entities += make_google_trends_snapshot(
                "flat_trend", flat_vals, ts)

            # new_trend: only in the last snapshot
            if i == 3:
                entities += make_google_trends_snapshot(
                    "new_trend", [25], ts)

            filepath = os.path.join(tmpdir, f"snapshot_{ts_str}.json")
            write_snapshot(filepath, "google_trends",
                           {"keywords": ["rocket_trend", "flat_trend"],
                            "geo": "PK", "fetched_at": ts.isoformat()},
                           entities)

        snapshots = load_all_snapshots([], tmpdir)
        assert len(snapshots) == 4, f"Expected 4, got {len(snapshots)}"

        results = score_all_trends(snapshots)
        by_name = {r["entity_name"]: r for r in results}

        rocket = by_name.get("rocket_trend")
        flat = by_name.get("flat_trend")
        new = by_name.get("new_trend")

        assert rocket and flat and new
        assert rocket["score"] > flat["score"]
        assert new["is_new"] is True
        assert 0 < new["score"] <= NEW_ENTITY_SCORE
        assert rocket["velocity"] > 0
        assert abs(flat["velocity"]) < 0.01

        for name, r in by_name.items():
            assert r["age_hours"] >= 0, f"{name} has negative age"

        print("  PASS end_to_end_single_source")
        print(f"    rocket: score={rocket['score']}, "
              f"vel={rocket['velocity']}, age={rocket['age_hours']:.0f}h")
        print(f"    flat:   score={flat['score']}, "
              f"vel={flat['velocity']}, age={flat['age_hours']:.0f}h")
        print(f"    new:    score={new['score']}, "
              f"is_new={new['is_new']}, age={new['age_hours']:.0f}h")

    finally:
        shutil.rmtree(tmpdir)


def test_multi_source_scoring():
    """Scoring across mixed sources in one category.

    Creates snapshots from google_trends, youtube_trending, and rss,
    all in the Food & Beverage category, and verifies:
      - Entities from all sources are scored
      - An entity appearing in multiple snapshots gets velocity
      - A new entity (single observation) gets modest score
      - No negative ages
    """
    tmpdir = tempfile.mkdtemp(prefix="trend_test_multi_")
    try:
        # Relative to now, not a fixed date: scores decay with age, so a
        # hardcoded date makes this test start failing once it ages out.
        base_time = (datetime.now(timezone.utc) - timedelta(hours=73)).replace(microsecond=0)

        # Snapshot 1 (t=0): google trends + youtube
        ts1 = base_time
        ts1_str = ts1.strftime("%Y%m%d_%H%M%S")

        gt_entities = make_google_trends_snapshot(
            "bbq recipes", [30, 35, 40], ts1, geo="PK")
        yt_entities = make_youtube_snapshot([
            ("Epic BBQ Cookoff", 500000),
            ("Street Food Tour", 200000),
        ], ts1, geo="PK")

        filepath1 = os.path.join(tmpdir, f"snapshot_{ts1_str}.json")
        write_snapshot(filepath1, "google_trends",
                       {"keywords": ["bbq recipes"], "geo": "PK",
                        "fetched_at": ts1.isoformat()},
                       gt_entities)

        filepath1b = os.path.join(
            tmpdir, f"snapshot_{(ts1 + timedelta(seconds=1)).strftime('%Y%m%d_%H%M%S')}.json")
        write_snapshot(filepath1b, "youtube_trending",
                       {"region_code": "PK", "fetched_at": ts1.isoformat()},
                       yt_entities)

        # Snapshot 2 (t=48h): google trends + youtube + rss
        ts2 = base_time + timedelta(hours=48)
        ts2_str = ts2.strftime("%Y%m%d_%H%M%S")

        gt_entities2 = make_google_trends_snapshot(
            "bbq recipes", [50, 60, 70], ts2, geo="PK")
        yt_entities2 = make_youtube_snapshot([
            ("Epic BBQ Cookoff", 1200000),
            ("Street Food Tour", 250000),
            ("New Grilling Hack", 100000),
        ], ts2, geo="PK")
        rss_entities = make_rss_snapshot([
            ("Best BBQ Techniques 2026", "Food & Beverage"),
            ("Grilling Safety Tips", "Food & Beverage"),
        ], ts2)

        filepath2 = os.path.join(tmpdir, f"snapshot_{ts2_str}.json")
        write_snapshot(filepath2, "google_trends",
                       {"keywords": ["bbq recipes"], "geo": "PK",
                        "fetched_at": ts2.isoformat()},
                       gt_entities2)

        filepath2b = os.path.join(
            tmpdir, f"snapshot_{(ts2 + timedelta(seconds=1)).strftime('%Y%m%d_%H%M%S')}.json")
        write_snapshot(filepath2b, "youtube_trending",
                       {"region_code": "PK", "fetched_at": ts2.isoformat()},
                       yt_entities2)

        filepath2c = os.path.join(
            tmpdir, f"snapshot_{(ts2 + timedelta(seconds=2)).strftime('%Y%m%d_%H%M%S')}.json")
        write_snapshot(filepath2c, "rss",
                       {"total_articles": 2, "fetched_at": ts2.isoformat()},
                       rss_entities)

        # Load and score
        snapshots = load_all_snapshots([], tmpdir)
        assert len(snapshots) == 5, f"Expected 5, got {len(snapshots)}"

        results = score_all_trends(snapshots)
        by_name = {r["entity_name"]: r for r in results}

        # Verify entities from all sources exist
        sources_seen = set(r["source"] for r in results)
        assert "google_trends" in sources_seen
        assert "youtube_trending" in sources_seen
        assert "rss" in sources_seen

        # bbq recipes should have velocity (appeared in 2 snapshots)
        bbq = by_name.get("bbq recipes")
        assert bbq is not None
        assert bbq["num_observations"] >= 2
        assert bbq["velocity"] is not None
        assert bbq["velocity"] > 0, "bbq recipes should be rising"

        # epic bbq cookoff: appeared twice with growing views
        epic = by_name.get("epic bbq cookoff")
        assert epic is not None
        assert epic["velocity"] > 0

        # new grilling hack: only in snapshot 2 -> is_new
        grilling = by_name.get("new grilling hack")
        assert grilling is not None
        assert grilling["is_new"] is True
        assert 0 < grilling["score"] <= NEW_ENTITY_SCORE

        # RSS entities exist
        bbq_tech = by_name.get("best bbq techniques 2026")
        assert bbq_tech is not None
        assert bbq_tech["source"] == "rss"

        # No negative ages anywhere
        for name, r in by_name.items():
            assert r["age_hours"] >= 0, f"{name} has negative age"

        print("  PASS multi_source_scoring")
        print(f"    Entities scored: {len(results)}")
        print(f"    Sources: {sorted(sources_seen)}")
        print(f"    bbq recipes:  score={bbq['score']}, "
              f"vel={bbq['velocity']}, obs={bbq['num_observations']}")
        print(f"    epic cookoff: score={epic['score']}, "
              f"vel={epic['velocity']}, obs={epic['num_observations']}")
        print(f"    new grilling: score={grilling['score']}, "
              f"is_new={grilling['is_new']}")

    finally:
        shutil.rmtree(tmpdir)


def test_flat_presence_entity_keeps_score():
    """An RSS article seen twice (value fixed at 1.0) must not drop to 0.

    Its velocity is exactly 0 -- presence-only sources never change value
    -- and still being on the list is a signal, not the absence of one.
    """
    tmpdir = tempfile.mkdtemp(prefix="trend_test_")
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for i, ts in enumerate([now - timedelta(hours=6), now - timedelta(hours=1)]):
            write_snapshot(
                os.path.join(tmpdir, f"snapshot_rss_{ts.strftime('%Y%m%d_%H%M%S')}.json"),
                "rss", {}, make_rss_snapshot([("Pistachio Kunafa Craze", "Food & Beverage")], ts))

        results = score_all_trends(load_all_snapshots([], tmpdir))
        assert len(results) == 1
        r = results[0]
        assert r["num_observations"] == 2 and r["velocity"] == 0
        assert r["score"] > 0.2, f"flat presence entity scored {r['score']}"
        print(f"  PASS flat_presence_entity_keeps_score (score={r['score']})")
    finally:
        shutil.rmtree(tmpdir)


def test_score_bounds_and_direction():
    """Rising lifts above the first-seen baseline, falling drops below it."""
    baseline = compute_score(velocity=None, age_hours=1.0, is_new=True)
    flat     = compute_score(velocity=0.0, age_hours=1.0, is_new=False)
    falling  = compute_score(velocity=-0.5, age_hours=1.0, is_new=False)
    crashing = compute_score(velocity=-50.0, age_hours=1.0, is_new=False)
    rising   = compute_score(velocity=0.5, age_hours=1.0, is_new=False)
    assert flat == baseline
    assert 0 <= crashing < falling < baseline < rising <= 1
    print("  PASS score_bounds_and_direction")


def test_history_dirs():
    """History feeds velocity but never adds entities or double-counts.

    - an entity only in history (dropped off every list) is not returned
    - an entity in both gets two observations and a velocity
    - the same snapshot file in root and history is loaded once
    """
    root = tempfile.mkdtemp(prefix="trend_root_")
    hist = tempfile.mkdtemp(prefix="trend_hist_")
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        t_old, t_new = now - timedelta(hours=12), now - timedelta(hours=1)

        old_name = f"snapshot_google_trends_{t_old.strftime('%Y%m%d_%H%M%S')}.json"
        write_snapshot(os.path.join(hist, old_name), "google_trends", {},
                       make_google_trends_snapshot("eid sale", [100], t_old)
                       + make_google_trends_snapshot("old news", [500], t_old))

        new_name = f"snapshot_google_trends_{t_new.strftime('%Y%m%d_%H%M%S')}.json"
        new_entities = make_google_trends_snapshot("eid sale", [400], t_new)
        write_snapshot(os.path.join(root, new_name), "google_trends", {}, new_entities)
        # Already archived copy of the current file -- must not count twice.
        write_snapshot(os.path.join(hist, new_name), "google_trends", {}, new_entities)

        snapshots = load_all_snapshots([hist], root)
        assert len(snapshots) == 2, f"Expected 2 unique files, got {len(snapshots)}"
        assert sum(s["is_current"] for s in snapshots) == 1

        by_name = {r["entity_name"]: r for r in score_all_trends(snapshots)}
        assert "old news" not in by_name, "history-only entity resurfaced"
        eid = by_name["eid sale"]
        assert eid["num_observations"] == 2
        assert eid["velocity"] > 0
        assert eid["score"] > NEW_ENTITY_SCORE
        print(f"  PASS history_dirs (eid sale score={eid['score']})")
    finally:
        shutil.rmtree(root)
        shutil.rmtree(hist)


def test_regions_not_merged():
    """The same term in two regions is two entities, not one fake spike."""
    root = tempfile.mkdtemp(prefix="trend_root_")
    hist = tempfile.mkdtemp(prefix="trend_hist_")
    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        t_old, t_new = now - timedelta(hours=12), now - timedelta(hours=1)
        write_snapshot(
            os.path.join(hist, f"snapshot_google_trends_{t_old.strftime('%Y%m%d_%H%M%S')}.json"),
            "google_trends", {}, make_google_trends_snapshot("argentina", [5000], t_old, geo="US"))
        write_snapshot(
            os.path.join(root, f"snapshot_google_trends_{t_new.strftime('%Y%m%d_%H%M%S')}.json"),
            "google_trends", {}, make_google_trends_snapshot("argentina", [100], t_new, geo="PK"))

        results = score_all_trends(load_all_snapshots([hist], root))
        assert len(results) == 1
        assert results[0]["region"] == "PK"
        assert results[0]["is_new"] is True, "PK term picked up US history"
        print("  PASS regions_not_merged")
    finally:
        shutil.rmtree(root)
        shutil.rmtree(hist)


def test_top_trends_spread_across_sources():
    """'All' takes the best of each source before repeating one,
    but never puts a stale item ahead of a fresh one to do it."""
    def t(name, source, score, age):
        return {"entity_name": name, "source": source, "score": score, "age_hours": age}
    scores = [
        t("vogue 1", "rss", 0.50, 2), t("vogue 2", "rss", 0.49, 2),
        t("gn story", "google_news", 0.48, 3),   # same 'news' group as rss
        t("vogue 3", "rss", 0.47, 2),
        t("reddit post", "reddit", 0.30, 5),
        t("wiki page", "wikipedia", 0.45, 40),   # outside the 24h window
    ]
    top = [x["entity_name"] for x in select_top_trends(scores, per_group=3)["all"]]
    assert top == ["vogue 1", "reddit post", "vogue 2"], top
    # Per-source tabs are unchanged: pure ranking within the group.
    news = [x["entity_name"] for x in select_top_trends(scores, per_group=3)["news"]]
    assert news == ["vogue 1", "vogue 2", "gn story"], news
    print("  PASS top_trends_spread_across_sources")


if __name__ == "__main__":
    print("\nTrend Scoring Tests (shared schema)")
    print("-" * 40)

    tests = [
        test_parse_timestamp,
        test_compute_velocity,
        test_compute_score,
        test_end_to_end_single_source,
        test_multi_source_scoring,
        test_flat_presence_entity_keeps_score,
        test_score_bounds_and_direction,
        test_history_dirs,
        test_regions_not_merged,
        test_top_trends_spread_across_sources,
    ]

    passed = 0
    failed = 0
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
