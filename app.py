"""
app.py — Flask backend for the Trend Intelligence Agent
Usage:
    cd ~/Agent_For_Trend_Scouting_and_Marketing
    python3 app.py
    Open: http://localhost:5000
"""

import os
import sys
import json
import glob
import uuid
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

from demo_mode import is_demo_mode, resolve_preset_key
from trend_scoring import select_top_trends
from ad_formats import FORMATS_BY_KEY, grouped as grouped_formats

# Always resolve paths relative to this file's location
BASE_DIR    = Path(__file__).parent.resolve()
STATIC_DIR  = BASE_DIR / "static"
JOBS_DIR    = BASE_DIR / "jobs"

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


# ── Job state ─────────────────────────────────────────────────
# Every job gets its own directory (jobs/<job_id>/) and all subprocesses
# run with that directory as cwd, so business_profile.json,
# competitor_urls.json, business_website.json, and every snapshot/output
# glob are fully isolated per job -- concurrent submissions can no longer
# cross-contaminate each other's files.
jobs = {}


def make_runner(job_id, job_dir, log_key, status_key):
    """Build (log_step, run) helpers writing to one job's log/status keys.

    Each phase of a job owns its own log and status field so they can run
    independently -- and be polled independently -- without clobbering
    each other's state.
    """
    log = []

    def log_step(msg):
        log.append(msg)
        jobs[job_id][log_key] = "\n".join(log)

    def run(label, cmd):
        log_step(f"\n▶ {label}")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(job_dir), env=env
        )
        if result.stdout.strip():
            log_step(result.stdout.strip())
        if result.stderr.strip():
            log_step(result.stderr.strip())
        return result.returncode == 0

    return log_step, run


# ── Phase 1: fetch trends (no business input required) ─────────
# Deliberately knows nothing about any business. This is what lets the
# UI show "what's trending right now" to someone who has typed nothing
# at all -- fetching and business-matching are separate steps, and the
# expensive GPT call only happens if the user actually asks for it.

def run_trend_fetch(job_id, job_dir, geo):
    jobs[job_id]["status"] = "running"
    log_step, run = make_runner(job_id, job_dir, "log", "status")

    # Google's live "what's trending right now" feed -- no keyword needed
    run("Fetching realtime trends...", [
        sys.executable, str(BASE_DIR / "google_trending_now.py"),
        "--geo", geo
    ])

    # YouTube trending chart (requires YOUTUBE_API_KEY)
    run("Fetching YouTube trending...", [
        sys.executable, str(BASE_DIR / "youtube_trending.py"),
        "--geo", geo
    ])

    # RSS articles (no credentials needed), capped to the last few days.
    # --geo selects which regional news feeds join the topical ones.
    run("Fetching RSS articles...", [
        sys.executable, str(BASE_DIR / "rss_trends.py"),
        "--geo", geo
    ])

    # TikTok Creative Center hashtags (may fall back to labeled mock,
    # which trend_scoring.py then excludes from the ranked output)
    run("Fetching TikTok hashtags...", [
        sys.executable, str(BASE_DIR / "tiktok_creative_center.py"),
        "--geo", geo
    ])

    # Score freshness across this job's freshly-fetched snapshots
    # (--root-dir pins it to job_dir; the shared cross-run snapshots/
    # history directory is still picked up automatically since that's
    # resolved relative to the script's own location, not cwd)
    ok = run("Scoring trend freshness...", [
        sys.executable, str(BASE_DIR / "trend_scoring.py"),
        "--root-dir", str(job_dir)
    ])
    if not ok:
        jobs[job_id]["status"] = "error"
        log_step("❌ Failed at trend_scoring.py")
        return

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
    log_step, run = make_runner(job_id, job_dir, "match_log", "match_status")

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
    log = []

    def log_step(msg):
        log.append(msg)
        jobs[job_id]["campaign_log"] = "\n".join(log)

    log_step("▶ Generating campaign ideas...")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "campaign_ideas.py")],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(job_dir), env=env
    )
    if result.stdout.strip():
        log_step(result.stdout.strip())
    if result.stderr.strip():
        log_step(result.stderr.strip())

    if result.returncode != 0:
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
    log_step, run = make_runner(job_id, job_dir, "adcopy_log", "adcopy_status")

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

    t = threading.Thread(target=run_trend_fetch,
                         args=(job_id, job_dir, geo), daemon=True)
    t.start()
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
    job = jobs.get(job_id)
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

    t = threading.Thread(target=run_business_match,
                         args=(job_id, job_dir, txt_path, has_website),
                         daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/campaign-ideas", methods=["POST"])
def campaign_ideas_route():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data received"}), 400

    job_id = data.get("job_id", "")
    job = jobs.get(job_id)
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

    t = threading.Thread(target=run_campaign_ideas,
                         args=(job_id, job_dir), daemon=True)
    t.start()
    return jsonify({"ok": True})


# ── Competitor Check (separate tab, separate flow) ──────────────
# Two modes, same underlying data: "check" gives a general positioning
# read on the competitors; "validate" assesses a specific idea the owner
# is considering against that same competitor data. Deliberately doesn't
# require having run a trend analysis first -- takes its own minimal
# business context directly.

def run_competitor_check(job_id, job_dir, mode):
    jobs[job_id]["status"] = "running"
    log = []

    def log_step(msg):
        log.append(msg)
        jobs[job_id]["log"] = "\n".join(log)

    def run(label, cmd):
        log_step(f"\n▶ {label}")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(job_dir), env=env
        )
        if result.stdout.strip():
            log_step(result.stdout.strip())
        if result.stderr.strip():
            log_step(result.stderr.strip())
        jobs[job_id]["log"] = "\n".join(log)
        return result.returncode == 0

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
    jobs[job_id]["log"] = "\n".join(log)


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

    t = threading.Thread(target=run_competitor_check,
                         args=(job_id, job_dir, mode), daemon=True)
    t.start()
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
    job = jobs.get(job_id)
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

    t = threading.Thread(target=run_ad_copy, args=(job_id, job_dir),
                         daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


if __name__ == "__main__":
    STATIC_DIR.mkdir(exist_ok=True)
    JOBS_DIR.mkdir(exist_ok=True)
    print(f"\n🚀 Trend Intelligence Agent")
    print(f"   Serving from: {BASE_DIR}")
    print(f"   Open: http://localhost:5000\n")
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, port=5000)
