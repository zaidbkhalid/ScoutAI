"""
run.py -- Trend Intelligence Agent (Full Pipeline)
--------------------------------------------------
One command runs everything:
  1. Fetch trends from all sources (Google realtime, YouTube, RSS, TikTok) --
     no keywords needed, these are all live "what's trending right now"
     feeds, and RSS is capped to the last few days (see rss_trends.py)
  2. Parse business .txt into structured profile
  3. Score trend freshness across snapshots
  4. Analyze trends against the business
  5. Generate HTML report + JSON output

Usage:
    python run.py my_business.txt
    python run.py my_business.txt --geo PK
    python run.py my_business.txt --skip-fetch   # use existing snapshots
"""

import sys
import os
import glob
import argparse
import subprocess
from pathlib import Path


def run_step(label, cmd):
    print(f"\n{'-'*50}")
    print(f"  {label}")
    print(f"{'-'*50}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(cmd, capture_output=False, text=True, env=env)
    if result.returncode != 0:
        print(f"\n  Step failed: {label}")
        return False
    return True


def find_latest(pattern):
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def main():
    parser = argparse.ArgumentParser(description="Trend Intelligence Agent")
    parser.add_argument("business_txt",
                        help="Path to your business description .txt file")
    parser.add_argument("--geo", default="PK",
                        help="Country code (default: PK)")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip fetching trends and use existing snapshots")
    args = parser.parse_args()

    print("\n" + "=" * 50)
    print("  Trend Intelligence Agent")
    print("=" * 50)
    print(f"\n  Business : {args.business_txt}")
    print(f"  Geo      : {args.geo}")
    print()

    BASE = str(Path(__file__).parent.resolve())

    # -- Step 1: Fetch Trends from all sources --
    if not args.skip_fetch:
        ok = run_step("Step 1a: Fetching realtime trends (Google)...", [
            sys.executable, str(Path(BASE) / "google_trending_now.py"),
            "--geo", args.geo,
        ])
        if not ok:
            print("  Realtime trends fetch failed. Continuing...")

        ok = run_step("Step 1b: Fetching YouTube trending...", [
            sys.executable, str(Path(BASE) / "youtube_trending.py"),
            "--geo", args.geo,
        ])
        if not ok:
            print("  YouTube fetch failed. Continuing...")

        ok = run_step("Step 1c: Fetching RSS articles...", [
            sys.executable, str(Path(BASE) / "rss_trends.py"),
        ])
        if not ok:
            print("  RSS fetch failed. Continuing...")

        ok = run_step("Step 1d: Fetching TikTok hashtags...", [
            sys.executable,
            str(Path(BASE) / "tiktok_creative_center.py"),
            "--geo", args.geo,
        ])
        if not ok:
            print("  TikTok fetch failed. Continuing...")

    # -- Step 2: Parse Business --
    ok = run_step("Step 2: Parsing business profile...",
                  [sys.executable,
                   str(Path(BASE) / "parse_business.py"),
                   args.business_txt])
    if not ok:
        print("  Cannot continue without business profile.")
        sys.exit(1)

    # -- Step 3: Score Trends --
    ok = run_step("Step 3: Scoring trend freshness...",
                  [sys.executable,
                   str(Path(BASE) / "trend_scoring.py")])
    if not ok:
        print("  Trend scoring failed. Continuing with raw data...")

    # -- Step 4: Analyze --
    scores_json = find_latest("trend_scores_*.json")

    cmd = [sys.executable, str(Path(BASE) / "analyze_trends.py")]
    if scores_json:
        cmd += ["--scores", scores_json]

    ok = run_step("Step 4: Analyzing trends vs business...", cmd)
    if not ok:
        sys.exit(1)

    # -- Step 5: Generate Report --
    analysis_json = find_latest("trend_analysis_*.json")
    ok = run_step("Step 5: Generating HTML report...",
                  [sys.executable,
                   str(Path(BASE) / "generate_report.py"),
                   analysis_json])
    if not ok:
        sys.exit(1)

    # -- Done --
    report = find_latest("trend_report_*.html")
    print("\n" + "=" * 50)
    print("  Pipeline complete!")
    print("=" * 50)
    print(f"\n  JSON analysis : {analysis_json}")
    print(f"  HTML report   : {report}")
    if report:
        print(f"\n  Open in browser:")
        print(f"  file://{Path(report).resolve()}")
    print()


if __name__ == "__main__":
    main()
