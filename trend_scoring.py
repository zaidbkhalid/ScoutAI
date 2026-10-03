"""
trend_scoring.py
----------------
Computes real freshness/velocity scores from historical trend snapshots.

Instead of asking GPT-4o to guess whether a trend is emerging or declining,
this module measures it from actual data across multiple snapshot files.

Snapshots use the shared schema (see snapshot_schema.py):
  snapshot_*.json files containing an "entities" array.

Output:
  trend_scores_<timestamp>.json -- ranked list of entities with computed scores

History:
  Velocity needs the same entity observed more than once. Snapshots in
  --root-dir are this run's fetch; snapshots in --snapshot-dir (and the
  snapshots/ directory next to this script, picked up automatically) are
  history. The web UI archives every fetch into snapshots/ after scoring
  it, so later fetches can measure velocity against earlier ones.

Usage:
    python trend_scoring.py
    python trend_scoring.py --snapshot-dir snapshots
"""

import json
import glob
import math
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path

from brand_safety import is_brand_safe, first_match


# -- Config --
AGE_HALF_LIFE_HOURS = 72    # Score halves every N hours of age
NEW_ENTITY_SCORE = 0.5      # Ceiling for entities seen only once
# How much of a first-observation score is fixed vs. earned by signal
# strength. On a first run every entity is "new", so without this every
# score collapses to exactly NEW_ENTITY_SCORE and the ranking is
# arbitrary. A term Google reports at 5000+ searches is a genuinely
# stronger signal than one at 100+, even on first sight -- this lets that
# show up, while capping it at NEW_ENTITY_SCORE so a single observation
# can never outrank the same-strength entity measured to be rising.
NEW_SIGNAL_FLOOR = 0.6      # 0.6 -> new-entity scores span 0.3 .. 0.5
# ------------


def parse_timestamp_from_filename(filename):
    """Extract datetime from filenames like snapshot_20260402_170603.json.

    Filename timestamps are treated as UTC.  Older snapshots created on a
    non-UTC machine may be off by that machine's UTC offset (a few hours),
    which is acceptable given the 72-hour age-decay half-life used in scoring.
    """
    m = re.search(r"(\d{8})_(\d{6})", filename)
    if not m:
        return None
    try:
        naive = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        return naive.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_all_snapshots(snapshot_dirs, root_dir):
    """Load every snapshot_*.json from the given directories and root.

    Each file must have an "entities" array in the shared schema.
    Returns a list of (timestamp, filename, entities) dicts sorted by time.

    Snapshots from root_dir are marked is_current (this run's fetch);
    those from snapshot_dirs are history, used only for velocity. A
    filename seen in more than one place is loaded once -- root first --
    since the same file twice would read as two observations at the same
    instant.
    """
    snapshots = []
    seen = set()
    pattern = "snapshot_*.json"

    search_paths = [(root_dir, True)] + [(d, False) for d in snapshot_dirs]
    for base, is_current in search_paths:
        for filepath in glob.glob(str(Path(base) / pattern)):
            name = Path(filepath).name
            if name in seen:
                continue
            ts = parse_timestamp_from_filename(name)
            if ts is None:
                continue
            try:
                data = json.loads(
                    Path(filepath).read_text(encoding="utf-8"))
                entities = data.get("entities", [])
                if not isinstance(entities, list):
                    continue
                snapshots.append({
                    "timestamp": ts,
                    "filename": name,
                    "source": data.get("source", "unknown"),
                    "entities": entities,
                    "is_current": is_current,
                })
                seen.add(name)
            except (json.JSONDecodeError, OSError):
                continue

    return sorted(snapshots, key=lambda s: s["timestamp"])


def parse_iso(value):
    """Parse an ISO8601 string to an aware datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def group_observations(snapshots):
    """Group entity observations across all snapshots.

    Keyed by (entity_id, region): entity ids don't carry the region, and
    the same term trending in PK and in US has unrelated volumes -- merging
    them would turn a region switch into a fake spike or collapse.
    """
    groups = {}
    for snap in snapshots:
        ts = snap["timestamp"]
        source = snap["source"]
        is_current = snap.get("is_current", True)
        for ent in snap["entities"]:
            eid = ent.get("entity_id", "")
            name = ent.get("entity_name", "").lower().strip()
            if not eid or not name:
                continue
            key = (eid, ent.get("region", ""))
            if key not in groups:
                groups[key] = {
                    "entity_id": eid,
                    "entity_name": name,
                    "entity_type": ent.get("entity_type", ""),
                    "source": ent.get("source", source),
                    "category": ent.get("category", ""),
                    "region": ent.get("region", ""),
                    "context": "",
                    "url": "",
                    "thumbnail": "",
                    "is_mock": False,
                    "in_current": False,
                    "observations": [],
                }
            if is_current:
                groups[key]["in_current"] = True
            # Some fetchers emit clearly-labeled synthetic placeholders
            # when a live source is unreachable (see
            # tiktok_creative_center.py). Honest in a snapshot, but it
            # must not reach a UI that presents these as real trends.
            if (ent.get("metrics", {}).get("is_mock")
                    or (ent.get("raw") or {}).get("mock")):
                groups[key]["is_mock"] = True
            # One-line reason this is trending (news headline, article
            # summary). Carried downstream so the relevance stage can
            # reason about WHY a bare term like "argentina" is spiking.
            ctx = (ent.get("raw") or {}).get("context", "")
            if ctx and not groups[key]["context"]:
                groups[key]["context"] = str(ctx)[:300]
            # Where the trend can be seen (the video, post, article, page)
            # and a thumbnail when the source has one, so the UI can link
            # every trend back to its real origin.
            raw = ent.get("raw") or {}
            link = raw.get("url") or raw.get("link") or raw.get("news_url") or ""
            if link and not groups[key]["url"]:
                groups[key]["url"] = str(link)
            if raw.get("thumbnail") and not groups[key]["thumbnail"]:
                groups[key]["thumbnail"] = str(raw["thumbnail"])
            groups[key]["observations"].append({
                "timestamp": ts,
                # When the content itself was published/spiked, which can
                # predate the moment we fetched it.
                "content_time": parse_iso(ent.get("observed_at")),
                "primary_value": ent.get("primary_value"),
                "rank": ent.get("rank"),
                "metrics": ent.get("metrics", {}),
            })

    # Sort each group's observations by timestamp
    for key in groups:
        groups[key]["observations"].sort(key=lambda o: o["timestamp"])

    return groups


def compute_velocity(observations):
    """Compute velocity: (latest - earliest) / max(|earliest|, 1) / hours.

    Returns None if fewer than 2 observations with numeric primary_value.
    """
    with_values = [
        o for o in observations
        if o.get("primary_value") is not None
    ]
    if len(with_values) < 2:
        return None

    earliest = with_values[0]
    latest = with_values[-1]

    elapsed = (latest["timestamp"] - earliest["timestamp"]).total_seconds()
    elapsed_hours = elapsed / 3600.0
    if elapsed_hours < 0.01:  # effectively same time
        return None

    v_early = earliest["primary_value"]
    v_late = latest["primary_value"]

    velocity = (v_late - v_early) / max(abs(v_early), 1.0) / elapsed_hours
    return round(velocity, 6)


def observation_signal(obs):
    """Raw strength of a single observation, before normalization.

    primary_value means different things per source (search volume,
    view count, presence), so this is only ever compared against other
    observations from the SAME source. feed_count multiplies it because
    a story carried by several feeds is a stronger signal than one.
    """
    pv = obs.get("primary_value")
    pv = float(pv) if isinstance(pv, (int, float)) else 1.0
    fc = obs.get("metrics", {}).get("feed_count")
    if isinstance(fc, (int, float)) and fc > 1:
        pv *= float(fc)
    return max(pv, 0.0)


def normalize_signals(signals, sources):
    """Log-scale each entity's signal into 0..1 against its own source.

    `signals` and `sources` map the same keys to a raw signal and a source
    name. Log rather than linear because volumes are heavy-tailed: one
    50000+ term shouldn't flatten every other term to ~0. Per source
    because view counts and search volumes aren't comparable.
    """
    maxima = {}
    for key, sig in signals.items():
        src = sources[key]
        maxima[src] = max(maxima.get(src, 0.0), sig)

    normalized = {}
    for key, sig in signals.items():
        source_max = maxima[sources[key]]
        norm = math.log1p(sig) / math.log1p(source_max) if source_max > 0 else 0.0
        normalized[key] = min(max(norm, 0.0), 1.0)
    return normalized


def compute_score(velocity, age_hours, is_new, signal_norm=1.0):
    """Final freshness score, 0..1.

    Every entity starts from a baseline: NEW_ENTITY_SCORE, scaled by its
    normalized signal strength and a gentle age decay. That is the whole
    score for an entity seen once.

    For an entity seen more than once, measured momentum
    (tanh(velocity) * exp(-age_hours / AGE_HALF_LIFE_HOURS)) moves it off
    that baseline: rising lifts it toward 1, falling pulls it toward 0,
    and flat leaves it at the baseline. Flat must not mean zero --
    presence-only sources (RSS articles, primary_value fixed at 1.0) never
    change value, and still being on a trending list is a signal, not the
    absence of one.
    """
    base = NEW_ENTITY_SCORE * math.exp(-age_hours / (AGE_HALF_LIFE_HOURS * 2))
    weight = NEW_SIGNAL_FLOOR + (1.0 - NEW_SIGNAL_FLOOR) * signal_norm
    baseline = base * weight

    if is_new or velocity is None:
        return round(baseline, 4)

    momentum = math.tanh(velocity) * math.exp(-age_hours / AGE_HALF_LIFE_HOURS)
    if momentum >= 0:
        return round(baseline + (1.0 - baseline) * momentum, 4)
    return round(baseline * (1.0 + momentum), 4)


def score_all_trends(snapshots):
    """Main scoring pipeline: group -> compute -> rank.

    When history snapshots are mixed in, only entities observed in the
    current fetch are scored and returned -- history exists to measure
    velocity, not to resurface trends that have since dropped off every
    list.
    """
    now = datetime.now(timezone.utc)

    groups = group_observations(snapshots)
    groups = {k: g for k, g in groups.items() if g["in_current"]}

    # Pre-pass: each entity's latest signal, normalized only against
    # others from the same source.
    signals = {k: observation_signal(g["observations"][-1])
               for k, g in groups.items()}
    signal_norms = normalize_signals(
        signals, {k: g["source"] for k, g in groups.items()})

    results = []
    for key, group in groups.items():
        obs = group["observations"]
        num_obs = len(obs)
        is_new = num_obs < 2
        first_seen = obs[0]["timestamp"]

        # Prefer the content's own timestamp (article publish time, search
        # spike time) over when we happened to fetch it -- otherwise every
        # entity in a fresh run reads as "0h old" regardless of whether it
        # broke an hour ago or three days ago.
        content_times = [o["content_time"] for o in obs if o["content_time"]]
        effective_start = first_seen
        if content_times:
            earliest_content = min(content_times)
            if earliest_content < first_seen:
                effective_start = earliest_content

        raw_age = (now - effective_start).total_seconds() / 3600.0
        age_hours = max(raw_age, 0.0)  # defensive floor

        velocity = compute_velocity(obs) if not is_new else None
        score = compute_score(velocity, age_hours, is_new, signal_norms[key])

        results.append({
            "entity_id": group["entity_id"],
            "entity_name": group["entity_name"],
            "entity_type": group["entity_type"],
            "source": group["source"],
            "category": group["category"],
            "region": group["region"],
            "context": group.get("context", ""),
            "url": group.get("url", ""),
            "thumbnail": group.get("thumbnail", ""),
            "is_mock": group.get("is_mock", False),
            "score": score,
            "velocity": velocity,
            "is_new": is_new,
            "age_hours": round(age_hours, 1),
            "num_observations": num_obs,
            "first_seen": effective_start.isoformat(),
            "latest_value": obs[-1].get("primary_value"),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


# -- Top-trend selection, grouped by source --------------------------
# The UI shows a small number of trends per source rather than a long
# ranked list, so the selection rule lives here next to the scoring it
# depends on.

RECENCY_WINDOW_HOURS = 24

# Which raw source names roll up into each UI group. Several sources can
# map to one group (Google has two fetchers), and a group with no live
# source configured simply comes back empty -- callers should say why
# rather than pretend the group doesn't exist.
SOURCE_GROUPS = {
    "google":  ("google_trending_now", "google_trends"),
    "youtube": ("youtube_trending", "youtube"),
    "news":    ("rss", "google_news"),
    "reddit":  ("reddit",),
    "wikipedia": ("wikipedia",),
}


def _rank_with_recency(items, limit, recency_hours):
    """Freshest-first within a recency window, then best of the rest.

    Sorting by score alone does NOT give "what's trending right now":
    the age decay in compute_score is deliberately gentle (half-life
    measured in days), so a high-signal three-day-old story outranks
    something that broke this morning. This puts everything inside the
    window first -- ordered by score among themselves -- and only falls
    back to older entities to fill the remaining slots.
    """
    fresh, older = [], []
    for it in items:
        age = it.get("age_hours")
        in_window = age is not None and age <= recency_hours
        it = dict(it, within_recency_window=in_window)
        (fresh if in_window else older).append(it)

    fresh.sort(key=lambda r: r.get("score", 0), reverse=True)
    older.sort(key=lambda r: r.get("score", 0), reverse=True)
    return (fresh + older)[:limit]


def source_group(source):
    for group, names in SOURCE_GROUPS.items():
        if source in names:
            return group
    return source


def _diverse_top(items, limit, recency_hours):
    """Top `limit` overall, spread across source groups.

    Without this the overall list is decided by whichever source has the
    most items -- three Vogue runway shows, three Google News stories --
    when the point of "All" is the best of every source. Recency still
    comes first: an in-window item from a repeated source beats a stale
    item from a new one. Passes, in order: in-window and new group,
    in-window, out-of-window and new group, anything.
    """
    ranked = _rank_with_recency(items, len(items), recency_hours)
    picked, groups = [], set()

    def take(fresh_only, new_group_only):
        for it in ranked:
            if len(picked) >= limit:
                return
            if it in picked:
                continue
            if fresh_only and not it["within_recency_window"]:
                continue
            g = source_group(it.get("source"))
            if new_group_only and g in groups:
                continue
            picked.append(it)
            groups.add(g)

    take(True, True)
    take(True, False)
    take(False, True)
    take(False, False)
    return picked


def select_top_trends(scores, per_group=5, recency_hours=RECENCY_WINDOW_HOURS):
    """Top `per_group` trends overall and per source group.

    Returns {"all": [...], "google": [...], "youtube": [...], ...}.
    Groups with no data come back as empty lists, which is a real
    answer (no API key configured, live source unavailable) and should
    be surfaced as such, not hidden.
    """
    selection = {"all": _diverse_top(scores, per_group, recency_hours)}

    for group, source_names in SOURCE_GROUPS.items():
        items = [s for s in scores if s.get("source") in source_names]
        selection[group] = _rank_with_recency(items, per_group, recency_hours)

    return selection


def filter_brand_unsafe(results, region=None):
    """Drop trends that are real but not campaign material.

    Returns (kept, dropped). See brand_safety.py for the rationale --
    briefly: these scores feed a UI that invites the user to turn the
    list into marketing, and a fatal crash does not belong there.
    """
    kept, dropped = [], []
    for r in results:
        if is_brand_safe(r.get("entity_name", ""), r.get("context", ""),
                         region=region):
            kept.append(r)
        else:
            r["_filtered_on"] = first_match(
                r.get("entity_name", ""), r.get("context", ""), region=region)
            dropped.append(r)
    return kept, dropped


def main():
    parser = argparse.ArgumentParser(description="Trend freshness scoring")
    parser.add_argument("--snapshot-dir", default=None, action="append",
                        help="Directory with historical snapshots (repeatable)")
    parser.add_argument("--root-dir", default=None,
                        help="Root dir for freshly-fetched JSONs "
                             "(default: script's directory)")
    parser.add_argument("--include-mock", action="store_true",
                        help="Keep clearly-labeled synthetic placeholder "
                             "entities that a fetcher emitted because its "
                             "live source was unreachable (excluded by "
                             "default so they never surface as real trends)")
    parser.add_argument("--include-unsafe", action="store_true",
                        help="Keep trends about death, violence, crime and "
                             "disaster, which are filtered out by default "
                             "since they are not campaign material "
                             "(see brand_safety.py)")
    parser.add_argument("--geo", default="",
                        help="Country code the fetch was for; adds that "
                             "market's own brand-safety filters (e.g. pork "
                             "and alcohol for PK and AE)")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.resolve()
    root_dir = args.root_dir or str(base_dir)

    snapshot_dirs = args.snapshot_dir or []
    default_snap = base_dir / "snapshots"
    if default_snap.is_dir() and str(default_snap) not in snapshot_dirs:
        snapshot_dirs.append(str(default_snap))

    print("Trend Freshness Scoring")
    print("-" * 40)
    print(f"  Root       : {root_dir}")
    print(f"  Snapshots  : {snapshot_dirs}")

    snapshots = load_all_snapshots(snapshot_dirs, root_dir)
    n_current = sum(1 for s in snapshots if s["is_current"])
    print(f"  Loaded     : {len(snapshots)} snapshot files "
          f"({n_current} current, {len(snapshots) - n_current} history)\n")

    if not snapshots:
        print("  WARNING: No snapshot files found. Nothing to score.")
        return

    results = score_all_trends(snapshots)

    mock_dropped = []
    if not args.include_mock:
        mock_dropped = [r for r in results if r.get("is_mock")]
        results = [r for r in results if not r.get("is_mock")]

    dropped = []
    if not args.include_unsafe:
        results, dropped = filter_brand_unsafe(results, region=args.geo)

    # Output
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(root_dir) / f"trend_scores_{ts}.json"
    output = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_count": len(snapshots),
            "snapshot_files": [s["filename"] for s in snapshots],
            "total_entities": len(results),
            "brand_safety_filtered": len(dropped),
            "mock_filtered": len(mock_dropped),
        },
        "scores": results,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"  Scored     : {len(results)} entities")
    if mock_dropped:
        print(f"  Excluded   : {len(mock_dropped)} synthetic placeholder "
              f"entities (live source unavailable)")
    if dropped:
        print(f"  Filtered   : {len(dropped)} not campaign-safe "
              f"(death/violence/crime/disaster/adult)")
    print(f"  Output     : {out_path.name}\n")

    # Print top results
    top = [r for r in results if r["score"] > 0][:10]
    if top:
        print("  Top Freshness Scores:")
        for r in top:
            new_tag = " [NEW]" if r["is_new"] else ""
            if r["velocity"] is not None:
                vel_str = f"vel={r['velocity']:.3f}"
            else:
                vel_str = "vel=N/A"
            print(f"    {r['score']:+.4f}  {vel_str}  "
                  f"age={r['age_hours']:.0f}h  "
                  f"obs={r['num_observations']}{new_tag}  "
                  f"{r['entity_name']}  [{r['source']}]")


if __name__ == "__main__":
    main()
