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
# can still never outrank a real measured velocity.
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
    """
    snapshots = []
    pattern = "snapshot_*.json"

    search_paths = [root_dir] + snapshot_dirs
    for base in search_paths:
        for filepath in glob.glob(str(Path(base) / pattern)):
            ts = parse_timestamp_from_filename(Path(filepath).name)
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
                    "filename": Path(filepath).name,
                    "source": data.get("source", "unknown"),
                    "entities": entities,
                })
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
    """Group entity observations across all snapshots by entity_id."""
    groups = {}
    for snap in snapshots:
        ts = snap["timestamp"]
        source = snap["source"]
        for ent in snap["entities"]:
            eid = ent.get("entity_id", "")
            name = ent.get("entity_name", "").lower().strip()
            if not eid or not name:
                continue
            if eid not in groups:
                groups[eid] = {
                    "entity_name": name,
                    "entity_type": ent.get("entity_type", ""),
                    "source": ent.get("source", source),
                    "category": ent.get("category", ""),
                    "region": ent.get("region", ""),
                    "context": "",
                    "is_mock": False,
                    "observations": [],
                }
            # Some fetchers emit clearly-labeled synthetic placeholders
            # when a live source is unreachable (see
            # tiktok_creative_center.py). Honest in a snapshot, but it
            # must not reach a UI that presents these as real trends.
            if (ent.get("metrics", {}).get("is_mock")
                    or (ent.get("raw") or {}).get("mock")):
                groups[eid]["is_mock"] = True
            # One-line reason this is trending (news headline, article
            # summary). Carried downstream so the relevance stage can
            # reason about WHY a bare term like "argentina" is spiking.
            ctx = (ent.get("raw") or {}).get("context", "")
            if ctx and not groups[eid]["context"]:
                groups[eid]["context"] = str(ctx)[:300]
            groups[eid]["observations"].append({
                "timestamp": ts,
                # When the content itself was published/spiked, which can
                # predate the moment we fetched it.
                "content_time": parse_iso(ent.get("observed_at")),
                "primary_value": ent.get("primary_value"),
                "rank": ent.get("rank"),
                "metrics": ent.get("metrics", {}),
            })

    # Sort each group's observations by timestamp
    for eid in groups:
        groups[eid]["observations"].sort(key=lambda o: o["timestamp"])

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


def normalize_signals(signals_by_source):
    """Log-scale each source's signals into 0..1, per source.

    Log rather than linear because volumes are heavy-tailed: one 50000+
    term shouldn't flatten every other term to ~0.
    """
    maxima = {}
    for source, values in signals_by_source.items():
        maxima[source] = max(values) if values else 0.0
    return maxima


def compute_score(velocity, age_hours, is_new, signal_norm=1.0):
    """Final freshness score:
      - tanh(velocity) bounded so outliers cannot dominate
      - multiplied by exp(-age_hours / AGE_HALF_LIFE_HOURS) for age decay
      - new entities (1 observation) are capped at NEW_ENTITY_SCORE and
        ordered among themselves by normalized signal strength
    """
    if is_new:
        base = NEW_ENTITY_SCORE * math.exp(
            -age_hours / (AGE_HALF_LIFE_HOURS * 2))
        weight = NEW_SIGNAL_FLOOR + (1.0 - NEW_SIGNAL_FLOOR) * signal_norm
        return round(base * weight, 4)

    if velocity is None:
        return 0.0

    bounded_vel = math.tanh(velocity)
    age_decay = math.exp(-age_hours / AGE_HALF_LIFE_HOURS)
    return round(bounded_vel * age_decay, 4)


def score_all_trends(snapshots):
    """Main scoring pipeline: group -> compute -> rank."""
    now = datetime.now(timezone.utc)

    groups = group_observations(snapshots)

    # Pre-pass: collect each entity's latest signal, bucketed by source,
    # so signals are only ever normalized against comparable ones.
    signals = {}
    signals_by_source = {}
    for eid, group in groups.items():
        sig = observation_signal(group["observations"][-1])
        signals[eid] = sig
        signals_by_source.setdefault(group["source"], []).append(sig)
    source_maxima = normalize_signals(signals_by_source)

    results = []
    for eid, group in groups.items():
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

        source_max = source_maxima.get(group["source"], 0.0)
        if source_max > 0:
            signal_norm = math.log1p(signals[eid]) / math.log1p(source_max)
        else:
            signal_norm = 0.0
        signal_norm = min(max(signal_norm, 0.0), 1.0)

        velocity = compute_velocity(obs) if not is_new else None
        score = compute_score(velocity, age_hours, is_new, signal_norm)

        results.append({
            "entity_id": eid,
            "entity_name": group["entity_name"],
            "entity_type": group["entity_type"],
            "source": group["source"],
            "category": group["category"],
            "region": group["region"],
            "context": group.get("context", ""),
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


def filter_brand_unsafe(results):
    """Drop trends that are real but not campaign material.

    Returns (kept, dropped). See brand_safety.py for the rationale --
    briefly: these scores feed a UI that invites the user to turn the
    list into marketing, and a fatal crash does not belong there.
    """
    kept, dropped = [], []
    for r in results:
        if is_brand_safe(r.get("entity_name", ""), r.get("context", "")):
            kept.append(r)
        else:
            r["_filtered_on"] = first_match(
                r.get("entity_name", ""), r.get("context", ""))
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
    print(f"  Loaded     : {len(snapshots)} snapshot files\n")

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
        results, dropped = filter_brand_unsafe(results)

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
              f"(death/violence/crime/disaster)")
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
