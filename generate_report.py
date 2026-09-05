"""
generate_report.py — clean, light, minimal HTML report
"""

import sys, json, glob
from pathlib import Path
from datetime import datetime


def find_latest(pattern):
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def timing_pill(t):
    t = t.lower()
    styles = {
        "act now":    ("bg:#1a1a1a;color:#fff",           "⚡ Act Now"),
        "this week":  ("bg:#e8f4e8;color:#1a6b1a",        "✓ This Week"),
        "plan ahead": ("bg:#fff8e8;color:#7a5a00",        "↗ Plan Ahead"),
        "skip":       ("bg:#f5f5f5;color:#999",           "— Skip"),
    }
    style, label = styles.get(t, ("bg:#f5f5f5;color:#999", t))
    css = style.replace("bg:", "background:").replace(";color:", ";color:")
    return f'<span style="display:inline-block;{css};padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:.03em">{label}</span>'


def lc_pill(s):
    s = s.lower()
    styles = {
        "emerging":   "#e8f4ff;color:#0066cc",
        "rising":     "#e8f8ee;color:#1a7a3a",
        "peak":       "#fff4e0;color:#c07000",
        "saturating": "#fef0f0;color:#cc3333",
        "declining":  "#f5f5f5;color:#999999",
    }
    col = styles.get(s, "#f5f5f5;color:#999")
    label = s.capitalize()
    return f'<span style="display:inline-block;background:{col};padding:3px 10px;border-radius:20px;font-size:11px;font-weight:500">{label}</span>'


def score_dot(n):
    n = int(n)
    dots = ""
    for i in range(10):
        filled = i < n
        color  = "#1a1a1a" if filled and n >= 7 else "#f0a500" if filled and n >= 4 else "#e05050" if filled else "#e8e8e8"
        dots  += f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:3px"></span>'
    return f'<div style="display:flex;align-items:center;gap:0;margin-top:4px">{dots}<span style="margin-left:8px;font-size:12px;color:#999;font-family:monospace">{n}/10</span></div>'


def render_trend(t, idx):
    timing   = t.get("timing", "skip")
    score    = t.get("relevance_score", 0)
    products = t.get("product_ideas", [])
    content  = t.get("content_ideas", [])
    bridge   = t.get("bridge_to_business", "")
    skip     = timing.lower() == "skip" or score < 3

    # Muted style for skipped/irrelevant trends
    opacity = "opacity:0.45" if skip else ""

    # Product ideas — compact list
    prod_html = ""
    if products and not skip:
        items = "".join(
            f'<div style="padding:10px 14px;background:#fafafa;border:1px solid #ebebeb;border-radius:6px;margin-bottom:8px">'
            f'<div style="font-weight:600;font-size:13px;margin-bottom:4px">{p.get("name","")}</div>'
            f'<div style="font-size:12px;color:#666;line-height:1.5">'
            f'<span style="margin-right:12px">🍫 {p.get("flavor","")}</span>'
            f'<span style="margin-right:12px">👤 {p.get("target","")}</span>'
            f'<span>📌 {p.get("use_case","")}</span>'
            f'</div></div>'
            for p in products[:2]
        )
        prod_html = f'<div style="margin-top:16px"><div style="font-size:11px;font-weight:700;letter-spacing:.08em;color:#999;text-transform:uppercase;margin-bottom:8px">Product Ideas</div>{items}</div>'

    # Content ideas — just hook + caption
    content_html = ""
    if content and not skip:
        items = "".join(
            f'<div style="padding:12px 14px;background:#fafafa;border:1px solid #ebebeb;border-radius:6px;margin-bottom:8px">'
            f'<div style="font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">{c.get("platform","")}</div>'
            f'<div style="font-size:13px;color:#333;margin-bottom:6px">🎯 {c.get("hook","")}</div>'
            f'<div style="font-size:13px;color:#555;font-style:italic;border-left:2px solid #ddd;padding-left:10px;line-height:1.55">{c.get("caption","")}</div>'
            f'</div>'
            for c in content[:2]
        )
        content_html = f'<div style="margin-top:12px"><div style="font-size:11px;font-weight:700;letter-spacing:.08em;color:#999;text-transform:uppercase;margin-bottom:8px">Content Ideas</div>{items}</div>'

    bridge_html = ""
    if bridge and not skip:
        bridge_html = f'<div style="font-size:13px;color:#555;line-height:1.6;margin-top:8px;padding:10px 12px;background:#f9f9f7;border-left:2px solid #ddd;border-radius:0 4px 4px 0">{bridge}</div>'

    return f'''<div style="border:1px solid #e8e8e8;border-radius:8px;padding:20px 22px;margin-bottom:12px;{opacity}">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div>
          <div style="font-size:17px;font-weight:700;letter-spacing:-.01em;margin-bottom:6px">{t.get("trend","")}</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
            {timing_pill(timing)}
            {lc_pill(t.get("lifecycle",""))}
          </div>
        </div>
        <div style="text-align:right;min-width:100px">
          <div style="font-size:11px;color:#aaa;margin-bottom:4px">Relevance</div>
          {score_dot(score)}
        </div>
      </div>
      {bridge_html}
      {prod_html}
      {content_html}
    </div>'''


def generate_html(data):
    biz    = data.get("business_snapshot", "")
    pos    = data.get("positioning_note", "")
    trends = data.get("trends", [])
    opps   = data.get("top_opportunities", [])
    skips  = data.get("skip_these", [])
    meta   = data.get("_meta", {})
    gen_at = meta.get("generated_at", datetime.now().isoformat())[:16].replace("T", " ")

    # Sort: act now → this week → plan ahead → skip
    order  = {"act now": 0, "this week": 1, "plan ahead": 2, "skip": 3}
    trends = sorted(trends, key=lambda x: (order.get(x.get("timing","").lower(), 4), -x.get("relevance_score", 0)))

    # Top opportunities — compact cards
    urgency_color = {"today": "#1a1a1a", "this week": "#1a6b1a", "this month": "#c07000"}
    opp_cards = ""
    for o in opps:
        col = urgency_color.get(o.get("urgency","").lower(), "#555")
        opp_cards += f'''<div style="border:1px solid #e8e8e8;border-radius:8px;padding:16px 18px;display:flex;align-items:flex-start;gap:14px">
          <div style="font-size:28px;font-weight:900;color:{col};line-height:1;min-width:28px">#{o.get("rank","")}</div>
          <div>
            <div style="font-size:10px;font-weight:700;color:{col};text-transform:uppercase;letter-spacing:.08em;margin-bottom:3px">{o.get("urgency","")}</div>
            <div style="font-size:14px;font-weight:600;margin-bottom:3px">{o.get("trend","")}</div>
            <div style="font-size:13px;color:#666">{o.get("one_line","")}</div>
          </div>
        </div>'''

    # Skip list — inline tags
    skip_tags = "".join(
        f'<div style="font-size:12px;color:#999;padding:6px 0;border-bottom:1px solid #f0f0f0">'
        f'<span style="color:#e05050;margin-right:6px">✗</span>{s}</div>'
        for s in skips
    )

    trend_cards = "\n".join(render_trend(t, i) for i, t in enumerate(trends))

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trend Report</title>
<link href="https://fonts.googleapis.com/css2?family=Cabinet+Grotesk:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  * {{ margin:0;padding:0;box-sizing:border-box }}
  body {{ background:#fff;color:#1a1a1a;font-family:'Cabinet Grotesk',sans-serif;font-size:15px;line-height:1.5 }}
  a {{ color:inherit;text-decoration:none }}
</style>
</head>
<body>

<!-- Header -->
<div style="border-bottom:1px solid #ebebeb;padding:20px 40px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;background:#fff;z-index:10">
  <div style="display:flex;align-items:center;gap:8px">
    <div style="width:8px;height:8px;border-radius:50%;background:#1a1a1a"></div>
    <span style="font-weight:700;font-size:14px">Trend Intelligence</span>
  </div>
  <div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#aaa">Generated {gen_at}</div>
</div>

<!-- Business summary -->
<div style="padding:40px 40px 0;max-width:900px;margin:0 auto">
  <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:.12em;margin-bottom:10px">Business</div>
  <div style="font-size:20px;font-weight:700;letter-spacing:-.02em;margin-bottom:8px">{biz}</div>
  <div style="font-size:14px;color:#666;border-left:2px solid #e0e0e0;padding-left:12px">{pos}</div>
</div>

<!-- Two col: opportunities + skip -->
<div style="padding:32px 40px;max-width:900px;margin:0 auto;display:grid;grid-template-columns:1fr 1fr;gap:20px">

  <div>
    <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:.12em;margin-bottom:12px">Top Opportunities</div>
    <div style="display:flex;flex-direction:column;gap:10px">{opp_cards}</div>
  </div>

  <div>
    <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:.12em;margin-bottom:12px">Skip These</div>
    <div style="border:1px solid #e8e8e8;border-radius:8px;padding:14px 16px">{skip_tags}</div>
  </div>

</div>

<!-- Divider -->
<div style="max-width:900px;margin:0 auto;padding:0 40px"><div style="border-top:1px solid #ebebeb"></div></div>

<!-- All trends -->
<div style="padding:32px 40px 80px;max-width:900px;margin:0 auto">
  <div style="font-family:'JetBrains Mono',monospace;font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:.12em;margin-bottom:20px">All Trends — Sorted by Priority</div>
  {trend_cards}
</div>

<!-- Footer -->
<div style="border-top:1px solid #ebebeb;padding:20px 40px;text-align:center;font-family:'JetBrains Mono',monospace;font-size:11px;color:#ccc">
  Trend Intelligence Agent · Google Trends + GPT-4o · {gen_at[:10]}
</div>

</body>
</html>'''


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else find_latest("trend_analysis_*.json")
    if not path or not Path(path).exists():
        print("❌ No trend_analysis_*.json found.")
        sys.exit(1)

    print(f"Generating report from: {path}")
    data     = json.loads(Path(path).read_text(encoding="utf-8"))
    html     = generate_html(data)
    out_path = path.replace(".json", ".html").replace("trend_analysis", "trend_report")
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"✅ Report → {out_path}")
    print(f"   Open: file://{Path(out_path).resolve()}")


if __name__ == "__main__":
    main()