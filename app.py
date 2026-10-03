"""
app.py — Flask backend for the Trend Intelligence Agent
Usage:
    cd ~/Agent_For_Trend_Scouting_and_Marketing
    python3 app.py
    Open: http://localhost:5000
"""

import os
import re
import sys
import json
import glob
import uuid
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_from_directory

from demo_mode import is_demo_mode, resolve_preset_key
from trend_scoring import select_top_trends, parse_timestamp_from_filename
from ad_formats import FORMATS_BY_KEY, grouped as grouped_formats

# Always resolve paths relative to this file's location
BASE_DIR    = Path(__file__).parent.resolve()
STATIC_DIR  = BASE_DIR / "static"
JOBS_DIR    = BASE_DIR / "jobs"
# Cross-run snapshot history. trend_scoring.py picks this directory up
# automatically, which is what lets it measure velocity across fetches.
HISTORY_DIR = BASE_DIR / "snapshots"

# Velocity is age-decayed with a 72h half-life, so history older than a
# week contributes almost nothing and is pruned.
HISTORY_RETENTION_DAYS = 7
JOB_RETENTION_DAYS     = 7

JOB_STATE_FILE = "job_state.json"
# job ids are generated server-side; anything else is rejected before it
# is ever joined onto a filesystem path.
JOB_ID_RE = re.compile(r"^(cc_)?\d{8}_\d{6}_[0-9a-f]{6}$")

app = Flask(__name__)

# ── Helpers ───────────────────────────────────────────────────

def find_latest(pattern, base_dir=None):
    base = Path(base_dir) if base_dir else BASE_DIR
    files = sorted(glob.glob(str(base / pattern)), reverse=True)
    return files[0] if files else None


def form_to_txt(data: dict) -> str:
    lines = []
    fields = [
        ("Business Name",       data.get("business_name","")),
        ("Tagline",             data.get("tagline","")),
        ("About",               data.get("about","")),
        ("Products / Services", data.get("products","")),
        ("Target Audience",     data.get("target_audience","")),
        ("What Makes Us Different", data.get("usp","")),
        ("Location",            data.get("location","")),
        ("Business Stage",      data.get("stage","")),
        ("Additional Notes",    data.get("extra","")),
    ]
    for label, val in fields:
        if val and val.strip():
            lines.append(f"{label}:\n{val.strip()}\n")

    tone     = data.get("brand_tone", [])
    channels = data.get("marketing_channels", [])
    if tone:
        lines.append(f"Brand Tone: {', '.join(tone)}\n")
    if channels:
        lines.append(f"Marketing Channels: {', '.join(channels)}\n")

    return "\n".join(lines)


def load_json_safe(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def compute_trend_selection(job_dir):
    """Top trends per source group from this job's freshest scores file.

    Pure data -- no GPT, no business profile needed -- which is what lets
    the UI show trends before anything is known about the business.
    """
    scores_json = find_latest("trend_scores_*.json", job_dir)
    if not scores_json:
        return None
    data = load_json_safe(scores_json) or {}
    scores = data.get("scores", [])
    if not scores:
        return None
    return select_top_trends(scores)


def archive_snapshots(job_dir):
    """Copy this job's snapshots into the shared history, then prune it.

    Runs after scoring, never before -- the job's own files must not be
    loaded twice in the same scoring pass. Each copy is written to a temp
    name and renamed into place so a concurrent scoring run never reads a
    half-written file.
    """
    HISTORY_DIR.mkdir(exist_ok=True)
    for src in Path(job_dir).glob("snapshot_*.json"):
        dest = HISTORY_DIR / src.name
        if dest.exists():
            continue
        tmp = HISTORY_DIR / f".{src.name}.tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)

    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_RETENTION_DAYS)
    for f in HISTORY_DIR.glob("snapshot_*.json"):
        ts = parse_timestamp_from_filename(f.name)
        if ts and ts < cutoff:
            f.unlink(missing_ok=True)


# ── Job state ─────────────────────────────────────────────────
# Every job gets its own directory (jobs/<job_id>/) and all subprocesses
# run with that directory as cwd, so business_profile.json,
# competitor_urls.json, business_website.json, and every snapshot/output
# glob are fully isolated per job -- concurrent submissions can no longer
# cross-contaminate each other's files.
#
# `jobs` is the live copy. Each job is also saved to
# jobs/<job_id>/job_state.json whenever a phase is queued or finishes, so
# a server restart doesn't orphan results the user is still looking at.
jobs = {}
jobs_lock = threading.Lock()

# Each phase's status key and the log key it writes to.
LOG_KEYS = {
    "status": "log",
    "match_status": "match_log",
    "campaign_status": "campaign_log",
    "adcopy_status": "adcopy_log",
}


def save_job(job_id):
    job = jobs.get(job_id)
    if job is None:
        return
    path = JOBS_DIR / job_id / JOB_STATE_FILE
    try:
        with jobs_lock:
            payload = json.dumps(dict(job), indent=2, ensure_ascii=False)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        # Persistence is a convenience; never let it take a job down.
        print(f"  warning: could not save state for job {job_id}: {e}")


def get_job(job_id):
    """The job's live state, falling back to its saved state on disk.

    A job restored from disk that was queued or running when it was saved
    can't be resumed -- the thread died with the old process -- so it is
    marked as an error rather than left polling forever.
    """
    if not job_id or not JOB_ID_RE.match(job_id):
        return None
    job = jobs.get(job_id)
    if job is not None:
        return job

    saved = load_json_safe(JOBS_DIR / job_id / JOB_STATE_FILE)
    if not isinstance(saved, dict):
        return None
    for status_key, log_key in LOG_KEYS.items():
        if saved.get(status_key) in ("queued", "running"):
            saved[status_key] = "error"
            saved[log_key] = (saved.get(log_key) or "") + \
                "\n❌ Interrupted by a server restart -- please run this step again."
    jobs[job_id] = saved
    return saved


def prune_old_jobs():
    """Delete job directories (and their in-memory state) past retention."""
    if not JOBS_DIR.is_dir():
        return
    # Job ids carry the server's local time (see the routes below), so
    # compare naive local times.
    cutoff = datetime.now() - timedelta(days=JOB_RETENTION_DAYS)
    for d in JOBS_DIR.iterdir():
        if not d.is_dir() or not JOB_ID_RE.match(d.name):
            continue
        ts = parse_timestamp_from_filename(d.name)
        if ts and ts.replace(tzinfo=None) < cutoff:
            jobs.pop(d.name, None)
            shutil.rmtree(d, ignore_errors=True)


def start_phase(target, job_id, status_key, *args):
    """Run one phase of a job in a background thread.

    Saves the job when the phase is queued and again when it ends, and
    turns an unexpected exception into an error status -- otherwise a
    crash inside the thread would leave the UI polling "running" forever.
    """
    save_job(job_id)

    def wrapper():
        try:
            target(job_id, *args)
        except Exception as e:
            log_key = LOG_KEYS[status_key]
            jobs[job_id][status_key] = "error"
            jobs[job_id][log_key] = (jobs[job_id].get(log_key) or "") + \
                f"\n❌ Unexpected error: {e}"
        finally:
            save_job(job_id)

    threading.Thread(target=wrapper, daemon=True).start()


def run_command(cmd, job_dir):
    """Run one pipeline script in a job's directory. Returns (ok, output)."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(job_dir), env=env
    )
    output = "\n".join(s for s in (result.stdout.strip(),
                                   result.stderr.strip()) if s)
    return result.returncode == 0, output


def make_runner(job_id, job_dir, log_key):
    """Build (log_step, run, run_parallel) helpers writing to one job's log key.

    Each phase of a job owns its own log and status field so they can run
    independently -- and be polled independently -- without clobbering
    each other's state.
    """
    log = []
    log_lock = threading.Lock()

    def log_step(msg):
        with log_lock:
            log.append(msg)
            jobs[job_id][log_key] = "\n".join(log)

    def run(label, cmd):
        log_step(f"\n▶ {label}")
        ok, output = run_command(cmd, job_dir)
        if output:
            log_step(output)
        return ok

    def run_parallel(steps):
        """Run independent [(label, cmd)] steps at once.

        Each step's label and output are logged together as it finishes,
        so concurrent steps never interleave in the log. Returns
        {label: ok}.
        """
        results = {}
        with ThreadPoolExecutor(max_workers=len(steps)) as pool:
            futures = {pool.submit(run_command, cmd, job_dir): label
                       for label, cmd in steps}
            for fut in as_completed(futures):
                label = futures[fut]
                ok, output = fut.result()
                results[label] = ok
                log_step(f"\n▶ {label}" + (f"\n{output}" if output else ""))
        return results

    return log_step, run, run_parallel


# ── Phase 1: fetch trends (no business input required) ─────────
# Deliberately knows nothing about any business. This is what lets the
# UI show "what's trending right now" to someone who has typed nothing
# at all -- fetching and business-matching are separate steps, and the
# expensive GPT call only happens if the user actually asks for it.

# Every source: (log label, script). Each takes --geo, needs no business
# input, and writes its own snapshot_<source>_<ts>.json, so they can all
# run at once -- the slowest (Reddit, when rate limited, can wait about a
# minute) sets the pace instead of the sum of all of them.
TREND_FETCHERS = [
    # Google's live "what's trending right now" feed -- no keyword needed
    ("Fetching Google trending searches...", "google_trending_now.py"),
    # Google News top stories + topic sections for the country
    ("Fetching Google News...",              "google_news.py"),
    # YouTube trending chart (requires YOUTUBE_API_KEY)
    ("Fetching YouTube trending...",         "youtube_trending.py"),
    # Curated RSS feeds, capped to the last few days; --geo adds the
    # country's regional feeds to the topical ones
    ("Fetching RSS articles...",             "rss_trends.py"),
    # Country subreddits (hot) + business-relevant subreddits (top today)
    ("Fetching Reddit...",                   "reddit_trends.py"),
    # Most-viewed Wikipedia pages (country when available, else global)
    ("Fetching Wikipedia most viewed...",    "wikipedia_trends.py"),
    # TikTok is deliberately absent -- see tiktok_creative_center.py's
    # docstring for why it can't be fetched without evading bot detection.
]


def run_trend_fetch(job_id, job_dir, geo):
    jobs[job_id]["status"] = "running"
    log_step, run, run_parallel = make_runner(job_id, job_dir, "log")

    run_parallel([
        (label, [sys.executable, str(BASE_DIR / script), "--geo", geo])
        for label, script in TREND_FETCHERS
    ])

    # Score freshness across this job's freshly-fetched snapshots
    # (--root-dir pins it to job_dir; the shared cross-run snapshots/
    # history directory is still picked up automatically since that's
    # resolved relative to the script's own location, not cwd)
    ok = run("Scoring trend freshness...", [
        sys.executable, str(BASE_DIR / "trend_scoring.py"),
        "--root-dir", str(job_dir), "--geo", geo
    ])
    if not ok:
        jobs[job_id]["status"] = "error"
        log_step("❌ Failed at trend_scoring.py")
        return

    # Only now add this fetch to the shared history, so the next fetch
    # can measure velocity against it.
    try:
        archive_snapshots(job_dir)
    except OSError as e:
        log_step(f"⚠ Could not archive snapshots to history: {e}")

    selection = compute_trend_selection(job_dir)
    if not selection:
        jobs[job_id]["status"] = "error"
        log_step("❌ No trends could be scored from any source.")
        return

    jobs[job_id]["trends_by_source"] = selection
    jobs[job_id]["status"] = "done"
    log_step("\n✅ Trends ready.")


# ── Phase 2: match those trends to a business ──────────────────
# Runs against the scores this job already fetched -- nothing is
# re-fetched. Tracked under its own match_* keys so phase 1's results
# stay intact and pollable while this runs.

def run_business_match(job_id, job_dir, txt_path, has_website):
    jobs[job_id]["match_status"] = "running"
    log_step, run, _ = make_runner(job_id, job_dir, "match_log")

    ok = run("Parsing business profile...",
             [sys.executable, str(BASE_DIR / "parse_business.py"), txt_path])
    if not ok:
        jobs[job_id]["match_status"] = "error"
        log_step("❌ Failed at parse_business.py")
        return

    # Whether this is one of the 3 DEMO_MODE presets, so the optional
    # legibility steps below can skip their real (but pointless -- the
    # output gets discarded anyway) fetch calls for them.
    profile = load_json_safe(job_dir / "business_profile.json") or {}
    business_name = profile.get("business_name", "")
    demo_preset = resolve_preset_key(business_name) if is_demo_mode() else None

    # Reuse the scores already fetched in phase 1 rather than re-fetching
    scores_json = find_latest("trend_scores_*.json", job_dir)
    if not scores_json:
        jobs[job_id]["match_status"] = "error"
        log_step("❌ No trend scores found for this job -- fetch trends first.")
        return

    ok = run("Matching trends to your business...", [
        sys.executable, str(BASE_DIR / "trend_analyzer.py"),
        "--scores", scores_json
    ])
    if not ok:
        jobs[job_id]["match_status"] = "error"
        log_step("❌ Failed at trend_analyzer.py")
        return

    analysis_json = find_latest("trend_analyzer_*.json", job_dir)
    jobs[job_id]["result_json"] = analysis_json
    if analysis_json:
        analysis = load_json_safe(analysis_json) or {}
        jobs[job_id]["top_trends"] = analysis.get("top_trends", [])
        jobs[job_id]["top_for_your_business"] = analysis.get("top_for_your_business", [])
        jobs[job_id]["business_snapshot"] = analysis.get("business_snapshot", "")

    # Optional: AI legibility check (structured data + answer-engine visibility)
    if has_website:
        if not demo_preset:
            run("Auditing website structured data...",
                [sys.executable, str(BASE_DIR / "structured_data_audit.py")])
            run("Checking AI answer engine visibility...",
                [sys.executable, str(BASE_DIR / "ai_visibility_check.py")])
        run("Synthesizing AI legibility report...",
            [sys.executable, str(BASE_DIR / "ai_legibility_synthesis.py")])
        legibility_json = find_latest("ai_legibility_report_*.json", job_dir)
        if legibility_json:
            jobs[job_id]["legibility_report"] = load_json_safe(legibility_json)

    jobs[job_id]["match_status"] = "done"
    log_step("\n✅ Matched to your business.")


# ── Stage 2: Campaign Ideas (from trends selected in Stage 1) ──
# Reuses the original job's directory (business_profile.json, etc.
# already live there) rather than a new job -- tracked with its own
# campaign_status/campaign_log/campaign_ideas keys on the same job dict
# so it doesn't collide with Stage 1's status/log/result fields, and the
# existing /api/status/<job_id> route already serves it with no changes.

def run_campaign_ideas(job_id, job_dir):
    jobs[job_id]["campaign_status"] = "running"
    log_step, run, _ = make_runner(job_id, job_dir, "campaign_log")

    ok = run("Generating campaign ideas...",
             [sys.executable, str(BASE_DIR / "campaign_ideas.py")])
    if not ok:
        jobs[job_id]["campaign_status"] = "error"
        log_step("❌ Failed at campaign_ideas.py")
        return

    out_json = find_latest("campaign_ideas_*.json", job_dir)
    if out_json:
        jobs[job_id]["campaign_ideas"] = load_json_safe(out_json)
    jobs[job_id]["campaign_status"] = "done"


# ── Stage 3: Ad Copy (from one campaign + chosen formats) ──────
# Same job directory again -- business_profile.json and the campaign are
# already there. Own adcopy_* keys so it can run without disturbing the
# trend, match or campaign results already on screen.

def run_ad_copy(job_id, job_dir):
    jobs[job_id]["adcopy_status"] = "running"
    log_step, run, _ = make_runner(job_id, job_dir, "adcopy_log")

    ok = run("Writing ad copy...",
             [sys.executable, str(BASE_DIR / "ad_copy.py")])
    if not ok:
        jobs[job_id]["adcopy_status"] = "error"
        log_step("❌ Failed at ad_copy.py")
        return

    out_json = find_latest("ad_copy_*.json", job_dir)
    if out_json:
        jobs[job_id]["ad_copy"] = load_json_safe(out_json)
    jobs[job_id]["adcopy_status"] = "done"


# ── Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/api/fetch-trends", methods=["POST"])
def fetch_trends():
    """Phase 1: fetch and score trends. Requires no business input."""
    data = request.get_json() or {}

    prune_old_jobs()
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"{ts}_{uuid.uuid4().hex[:6]}"
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    jobs[job_id] = {
        "status": "queued", "log": "Fetching trends...\n",
        "trends_by_source": None,
        "result_json": None, "business_snapshot": "",
        "top_trends": [], "top_for_your_business": [],
        "legibility_report": None,
        "match_status": None, "match_log": "",
        "campaign_status": None, "campaign_log": "", "campaign_ideas": None,
        "adcopy_status": None, "adcopy_log": "", "ad_copy": None,
    }

    geo = data.get("geo", "PK")

    start_phase(run_trend_fetch, job_id, "status", job_dir, geo)
    return jsonify({"job_id": job_id})


@app.route("/api/match-business", methods=["POST"])
def match_business():
    """Phase 2: match already-fetched trends to a business profile.

    Reuses the trend scores fetched by /api/fetch-trends for this job --
    nothing is re-fetched.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data received"}), 400

    job_id = data.get("job_id", "")
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found — fetch trends first"}), 404

    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        return jsonify({"error": "Job directory no longer exists"}), 404

    if not find_latest("trend_scores_*.json", job_dir):
        return jsonify({"error": "No trends fetched for this job yet"}), 400

    txt_path = str(job_dir / "business.txt")
    Path(txt_path).write_text(form_to_txt(data), encoding="utf-8")

    # Optional: business's own website URL, for the AI legibility check
    website = (data.get("website") or "").strip()
    has_website = bool(website)
    if has_website:
        (job_dir / "business_website.json").write_text(
            json.dumps({"website": website}, indent=2), encoding="utf-8")

    job["match_status"] = "queued"
    job["match_log"] = "Starting...\n"

    start_phase(run_business_match, job_id, "match_status",
                job_dir, txt_path, has_website)
    return jsonify({"ok": True})


@app.route("/api/campaign-ideas", methods=["POST"])
def campaign_ideas_route():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data received"}), 400

    job_id = data.get("job_id", "")
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        return jsonify({"error": "Job directory no longer exists"}), 404

    selected = data.get("selected_trends") or []
    if not selected:
        return jsonify({"error": "Select at least one trend"}), 400

    (job_dir / "selected_trends.json").write_text(
        json.dumps({"trends": selected}, indent=2), encoding="utf-8")

    job["campaign_status"] = "queued"
    job["campaign_log"] = "Starting...\n"
    job["campaign_ideas"] = None

    start_phase(run_campaign_ideas, job_id, "campaign_status", job_dir)
    return jsonify({"ok": True})


# ── Competitor Check (separate tab, separate flow) ──────────────
# Two modes, same underlying data: "check" gives a general positioning
# read on the competitors; "validate" assesses a specific idea the owner
# is considering against that same competitor data. Deliberately doesn't
# require having run a trend analysis first -- takes its own minimal
# business context directly.

def run_competitor_check(job_id, job_dir, mode):
    jobs[job_id]["status"] = "running"
    log_step, run, _ = make_runner(job_id, job_dir, "log")

    profile = load_json_safe(job_dir / "business_profile.json") or {}
    business_name = profile.get("business_name", "")
    demo_preset = resolve_preset_key(business_name) if is_demo_mode() else None

    if not demo_preset:
        run("Researching competitors...",
            [sys.executable, str(BASE_DIR / "competitor_research.py")])

    if mode == "validate":
        ok = run("Validating your idea against competitors...",
                  [sys.executable, str(BASE_DIR / "idea_validation.py")])
        if not ok:
            jobs[job_id]["status"] = "error"
            log_step("❌ Failed at idea_validation.py")
            return
        result_json = find_latest("idea_validation_*.json", job_dir)
        if result_json:
            jobs[job_id]["idea_validation"] = load_json_safe(result_json)
    else:
        ok = run("Synthesizing competitor analysis...",
                  [sys.executable, str(BASE_DIR / "competitor_synthesis.py")])
        if not ok:
            jobs[job_id]["status"] = "error"
            log_step("❌ Failed at competitor_synthesis.py")
            return
        result_json = find_latest("competitor_analysis_*.json", job_dir)
        if result_json:
            jobs[job_id]["competitor_analysis"] = load_json_safe(result_json)

    jobs[job_id]["status"] = "done"
    log_step("\n✅ Done!")


@app.route("/api/competitor-check", methods=["POST"])
def competitor_check():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data received"}), 400

    business_name = (data.get("business_name") or "").strip()
    if not business_name:
        return jsonify({"error": "Business name is required"}), 400

    mode = data.get("mode") if data.get("mode") in ("check", "validate") else "check"

    raw_competitors = data.get("competitors") or []
    competitor_urls = {
        c.get("name", "").strip(): c.get("url", "").strip()
        for c in raw_competitors
        if isinstance(c, dict) and c.get("name", "").strip() and c.get("url", "").strip()
    }
    if not competitor_urls:
        return jsonify({"error": "At least one competitor name + URL is required"}), 400

    idea = (data.get("idea") or "").strip()
    if mode == "validate" and not idea:
        return jsonify({"error": "Describe the idea you want validated"}), 400

    prune_old_jobs()
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_id = f"cc_{ts}_{uuid.uuid4().hex[:6]}"
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # No parse_business.py here -- this tab is deliberately lightweight and
    # separate from the trend-analysis flow, so business_profile.json is
    # built directly from the short form instead of GPT-extracted.
    (job_dir / "business_profile.json").write_text(json.dumps({
        "business_name": business_name,
        "additional_context": (data.get("about") or "").strip(),
    }, indent=2), encoding="utf-8")

    (job_dir / "competitor_urls.json").write_text(
        json.dumps(competitor_urls, indent=2), encoding="utf-8")

    if mode == "validate":
        (job_dir / "business_idea.json").write_text(
            json.dumps({"idea": idea}, indent=2), encoding="utf-8")

    jobs[job_id] = {
        "status": "queued", "log": "Starting...\n",
        "competitor_analysis": None, "idea_validation": None,
    }

    start_phase(run_competitor_check, job_id, "status", job_dir, mode)
    return jsonify({"job_id": job_id})


@app.route("/api/ad-copy-formats")
def ad_copy_formats():
    """The format catalogue, so the picker renders from one source."""
    return jsonify({"groups": grouped_formats()})


@app.route("/api/ad-copy", methods=["POST"])
def ad_copy_route():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data received"}), 400

    job_id = data.get("job_id", "")
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        return jsonify({"error": "Job directory no longer exists"}), 404

    campaign = data.get("campaign") or {}
    if not campaign:
        return jsonify({"error": "No campaign selected"}), 400

    formats = [f for f in (data.get("formats") or []) if f in FORMATS_BY_KEY]
    if not formats:
        return jsonify({"error": "Pick at least one format"}), 400

    (job_dir / "selected_campaign.json").write_text(
        json.dumps({"campaign": campaign, "formats": formats}, indent=2),
        encoding="utf-8")

    job["adcopy_status"] = "queued"
    job["adcopy_log"] = "Starting...\n"
    job["ad_copy"] = None

    start_phase(run_ad_copy, job_id, "adcopy_status", job_dir)
    return jsonify({"ok": True})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


if __name__ == "__main__":
    STATIC_DIR.mkdir(exist_ok=True)
    JOBS_DIR.mkdir(exist_ok=True)
    prune_old_jobs()
    print(f"\n🚀 Trend Intelligence Agent")
    print(f"   Serving from: {BASE_DIR}")
    print(f"   Open: http://localhost:5000\n")
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, port=5000)
