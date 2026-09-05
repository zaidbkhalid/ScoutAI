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
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, send_file

# Always resolve paths relative to this file's location
BASE_DIR    = Path(__file__).parent.resolve()
STATIC_DIR  = BASE_DIR / "static"

app = Flask(__name__)

# ── Helpers ───────────────────────────────────────────────────

def find_latest(pattern):
    files = sorted(glob.glob(str(BASE_DIR / pattern)), reverse=True)
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


# ── Job state ─────────────────────────────────────────────────
jobs = {}


def run_pipeline(job_id, txt_path, geo, keywords):
    jobs[job_id]["status"] = "running"
    log = []

    def log_step(msg):
        log.append(msg)
        jobs[job_id]["log"] = "\n".join(log)

    def run(label, cmd):
        log_step(f"\n▶ {label}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(BASE_DIR)
        )
        if result.stdout.strip():
            log_step(result.stdout.strip())
        if result.stderr.strip():
            log_step(result.stderr.strip())
        jobs[job_id]["log"] = "\n".join(log)
        return result.returncode == 0

    # Step 1: parse business
    ok = run("Parsing business profile...", [sys.executable, str(BASE_DIR / "parse_business.py"), txt_path])
    if not ok:
        jobs[job_id]["status"] = "error"
        log_step("❌ Failed at parse_business.py")
        return

    # Step 2: keyword trends — pass user keywords + geo as CLI args
    kw_str = ",".join([k.strip() for k in keywords.split(",")][:5])
    run("Fetching keyword trends...", [
        sys.executable, str(BASE_DIR / "google_trends.py"),
        "--keywords", kw_str,
        "--geo",      geo
    ])

    # Step 3: realtime trends — pass geo as CLI arg
    run("Fetching realtime trends...", [
        sys.executable, str(BASE_DIR / "google_trending_now.py"),
        "--geo", geo
    ])

    # Step 4: YouTube trending videos (requires YOUTUBE_API_KEY)
    run("Fetching YouTube trending...", [
        sys.executable, str(BASE_DIR / "youtube_trending.py"),
        "--geo", geo
    ])

    # Step 5: RSS feed articles (no credentials needed)
    run("Fetching RSS articles...", [
        sys.executable, str(BASE_DIR / "rss_trends.py")
    ])

    # Step 6: TikTok Creative Center hashtags (may fall back to mock)
    run("Fetching TikTok hashtags...", [
        sys.executable, str(BASE_DIR / "tiktok_creative_center.py"),
        "--geo", geo
    ])

    # Step 7: score freshness across all historical snapshots
    run("Scoring trend freshness...", [
        sys.executable, str(BASE_DIR / "trend_scoring.py")
    ])

    # Step 8: analyze
    scores_json = find_latest("trend_scores_*.json")
    cmd = [sys.executable, str(BASE_DIR / "analyze_trends.py")]
    if scores_json: cmd += ["--scores", scores_json]
    ok = run("Analyzing trends vs business...", cmd)
    if not ok:
        jobs[job_id]["status"] = "error"
        log_step("❌ Failed at analyze_trends.py")
        return

    # Step 9: generate report
    analysis_json = find_latest("trend_analysis_*.json")
    ok = run("Generating HTML report...",
             [sys.executable, str(BASE_DIR / "generate_report.py"), analysis_json])
    if not ok:
        jobs[job_id]["status"] = "error"
        log_step("❌ Failed at generate_report.py")
        return

    report_html = find_latest("trend_report_*.html")
    jobs[job_id]["status"]      = "done"
    jobs[job_id]["result_html"] = report_html
    jobs[job_id]["result_json"] = analysis_json
    log_step("\n✅ Done! Report is ready.")
    jobs[job_id]["log"] = "\n".join(log)


# ── Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data received"}), 400

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = str(BASE_DIR / f"business_{ts}.txt")
    Path(txt_path).write_text(form_to_txt(data), encoding="utf-8")

    job_id = ts
    jobs[job_id] = {"status": "queued", "log": "Pipeline starting...\n",
                    "result_html": None, "result_json": None}

    geo      = data.get("geo", "PK")
    keywords = data.get("trend_keywords", "PSL,rain Karachi,Eid,drama Pakistan,celebrity")

    t = threading.Thread(target=run_pipeline,
                         args=(job_id, txt_path, geo, keywords), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/report/<job_id>")
def get_report(job_id):
    job = jobs.get(job_id)
    if not job or not job.get("result_html"):
        return jsonify({"error": "Report not ready"}), 404
    return send_file(job["result_html"])


if __name__ == "__main__":
    STATIC_DIR.mkdir(exist_ok=True)
    print(f"\n🚀 Trend Intelligence Agent")
    print(f"   Serving from: {BASE_DIR}")
    print(f"   Open: http://localhost:5000\n")
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, port=5000)