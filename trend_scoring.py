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


# -- Config --
AGE_HALF_LIFE_HOURS = 72    # Score halves every N hours of age
NEW_ENTITY_SCORE = 0.5      # Flat score for entities seen only once
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
                    "observations": [],
                }
            groups[eid]["observations"].append({
                "timestamp": ts,
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


def compute_score(velocity, age_hours, is_new):
    """Final freshness score:
      - tanh(velocity) bounded so outliers cannot dominate
      - multiplied by exp(-age_hours / AGE_HALF_LIFE_HOURS) for age decay
      - new entities (1 observation) get a flat modest score
    """
    if is_new:
        return round(
            NEW_ENTITY_SCORE
            * math.exp(-age_hours / (AGE_HALF_LIFE_HOURS * 2)),
            4,
        )

    if velocity is None:
        return 0.0

    bounded_vel = math.tanh(velocity)
    age_decay = math.exp(-age_hours / AGE_HALF_LIFE_HOURS)
    return round(bounded_vel * age_decay, 4)


def score_all_trends(snapshots):
    """Main scoring pipeline: group -> compute -> rank."""
    now = datetime.now(timezone.utc)

    groups = group_observations(snapshots)

    results = []
    for eid, group in groups.items():
        obs = group["observations"]
        num_obs = len(obs)
        is_new = num_obs < 2
        first_seen = obs[0]["timestamp"]
        raw_age = (now - first_seen).total_seconds() / 3600.0
        age_hours = max(raw_age, 0.0)  # defensive floor

        velocity = compute_velocity(obs) if not is_new else None
        score = compute_score(velocity, age_hours, is_new)

        results.append({
            "entity_id": eid,
            "entity_name": group["entity_name"],
            "entity_type": group["entity_type"],
            "source": group["source"],
            "category": group["category"],
            "region": group["region"],
            "score": score,
            "velocity": velocity,
            "is_new": is_new,
            "age_hours": round(age_hours, 1),
            "num_observations": num_obs,
            "first_seen": first_seen.isoformat(),
            "latest_value": obs[-1].get("primary_value"),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def main():
    parser = argparse.ArgumentParser(description="Trend freshness scoring")
    parser.add_argument("--snapshot-dir", default=None, action="append",
                        help="Directory with historical snapshots (repeatable)")
    parser.add_argument("--root-dir", default=None,
                        help="Root dir for freshly-fetched JSONs "
                             "(default: script's directory)")
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

    # Output
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(root_dir) / f"trend_scores_{ts}.json"
    output = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_count": len(snapshots),
            "snapshot_files": [s["filename"] for s in snapshots],
            "total_entities": len(results),
        },
        "scores": results,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"  Scored     : {len(results)} entities")
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
