# ──────────────────────────────────────────────────────────────────────
# DISCONNECTED FROM PIPELINE — moved to unused/ on 2026-09-04
#
# This module was the original multi-source trend scout (Reddit, Twitter,
# Google Trends, RSS, Instagram). It was replaced by the current pipeline
# which uses google_trends.py + google_trending_now.py + analyze_trends.py.
#
# It is NOT wired into app.py or run.py.
#
# Why it's kept:
#   - The RSS-feed portion (get_rss_trends) works without any API credentials
#     and could serve as a supplementary data source in the future.
#   - The Google Trends functions overlap with google_trends.py and could
#     be merged if needed.
#
# Why it was disconnected:
#   - Reddit and Twitter integrations require API credentials that were
#     never configured (placeholder strings only).
#   - Instagram scraping uses an undocumented endpoint that is likely broken.
#   - The pipeline moved to a simpler Google-Trends-only approach.
# ──────────────────────────────────────────────────────────────────────

"""
Social Media Trend Scout
========================
Scouts for rising trends across Reddit, Twitter/X, Google Trends, and RSS feeds.

Requirements:
    pip install praw pytrends requests beautifulsoup4 feedparser pandas rich tweepy

Setup:
    - Reddit  : Create an app at https://www.reddit.com/prefs/apps  (script type)
    - Twitter : Get Bearer Token from https://developer.twitter.com
    - Google  : No key needed (pytrends uses unofficial API)
"""

import time
import feedparser
import requests
import pandas as pd
from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()

# ──────────────────────────────────────────────
# CONFIG  ── fill these in
# ──────────────────────────────────────────────
REDDIT_CLIENT_ID     = "YOUR_REDDIT_CLIENT_ID"
REDDIT_CLIENT_SECRET = "YOUR_REDDIT_CLIENT_SECRET"
REDDIT_USER_AGENT    = "TrendScout/1.0"

TWITTER_BEARER_TOKEN = "YOUR_TWITTER_BEARER_TOKEN"

# Keywords / topics you want to track
TRACK_KEYWORDS = ["AI", "crypto", "fashion", "startup"]

# Subreddits to monitor
SUBREDDITS = ["technology", "business", "marketing", "entrepreneur", "worldnews"]


# ══════════════════════════════════════════════
# 1. REDDIT TRENDS
# ══════════════════════════════════════════════
def get_reddit_trends(limit: int = 10) -> pd.DataFrame:
    """Fetch rising & hot posts from specified subreddits via Reddit API."""
    try:
        import praw
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )
        rows = []
        for sub_name in SUBREDDITS:
            sub = reddit.subreddit(sub_name)
            for post in sub.rising(limit=limit):
                rows.append({
                    "source":    f"r/{sub_name}",
                    "title":     post.title[:80],
                    "score":     post.score,
                    "comments":  post.num_comments,
                    "upvote_%":  f"{post.upvote_ratio*100:.0f}%",
                    "url":       f"https://reddit.com{post.permalink}",
                })
        df = pd.DataFrame(rows).sort_values("score", ascending=False)
        return df
    except Exception as e:
        console.print(f"[red]Reddit error:[/red] {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════
# 2. GOOGLE TRENDS
# ══════════════════════════════════════════════
def get_google_trends(keywords: list = None, timeframe: str = "now 1-d") -> pd.DataFrame:
    """
    Pull interest-over-time data from Google Trends.
    timeframe options: 'now 1-H', 'now 4-H', 'now 1-d', 'now 7-d', 'today 1-m'
    """
    try:
        from pytrends.request import TrendReq
        keywords = keywords or TRACK_KEYWORDS
        pytrends = TrendReq(hl="en-US", tz=0)
        rows = []
        for chunk in [keywords[i:i+5] for i in range(0, len(keywords), 5)]:
            pytrends.build_payload(chunk, timeframe=timeframe)
            df = pytrends.interest_over_time()
            if df.empty:
                continue
            latest = df.drop(columns=["isPartial"], errors="ignore").iloc[-1]
            for kw in chunk:
                if kw in latest:
                    rows.append({"keyword": kw, "interest": int(latest[kw])})
        result = pd.DataFrame(rows).sort_values("interest", ascending=False)
        return result
    except Exception as e:
        console.print(f"[red]Google Trends error:[/red] {e}")
        return pd.DataFrame()


def get_google_trending_searches(country: str = "united_states") -> pd.DataFrame:
    """Fetch today's top trending searches from Google."""
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=0)
        df = pytrends.trending_searches(pn=country)
        df.columns = ["trending_topic"]
        df.index = range(1, len(df) + 1)
        return df.head(20)
    except Exception as e:
        console.print(f"[red]Google Trending error:[/red] {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════
# 3. TWITTER / X TRENDS  (requires API v2 access)
# ══════════════════════════════════════════════
def get_twitter_trends(woeid: int = 1) -> pd.DataFrame:
    """
    Fetch Twitter trending topics.
    woeid=1  -> Worldwide
    woeid=2459115 -> New York, etc.
    Note: Requires Basic or higher Twitter API tier.
    """
    try:
        headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
        url = f"https://api.twitter.com/1.1/trends/place.json?id={woeid}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        trends = resp.json()[0]["trends"]
        rows = [
            {
                "rank":      i + 1,
                "topic":     t["name"],
                "tweet_vol": t.get("tweet_volume") or "N/A",
                "url":       t.get("url", ""),
            }
            for i, t in enumerate(trends[:20])
        ]
        return pd.DataFrame(rows)
    except Exception as e:
        console.print(f"[red]Twitter error:[/red] {e}")
        return pd.DataFrame()


def search_twitter_keyword(keyword: str, max_results: int = 20) -> pd.DataFrame:
    """Search recent tweets for a keyword (Twitter API v2)."""
    try:
        import tweepy
        client = tweepy.Client(bearer_token=TWITTER_BEARER_TOKEN, wait_on_rate_limit=True)
        resp = client.search_recent_tweets(
            query=f"{keyword} -is:retweet lang:en",
            max_results=min(max_results, 100),
            tweet_fields=["created_at", "public_metrics", "author_id"],
        )
        if not resp.data:
            return pd.DataFrame()
        rows = [
            {
                "text":    tweet.text[:80],
                "likes":   tweet.public_metrics["like_count"],
                "rt":      tweet.public_metrics["retweet_count"],
                "replies": tweet.public_metrics["reply_count"],
                "created": tweet.created_at,
            }
            for tweet in resp.data
        ]
        return pd.DataFrame(rows).sort_values("likes", ascending=False)
    except Exception as e:
        console.print(f"[red]Twitter search error:[/red] {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════
# 4. RSS FEED TRENDS  (no API key needed)
# ══════════════════════════════════════════════
RSS_FEEDS = {
    "TechCrunch":        "https://techcrunch.com/feed/",
    "The Verge":         "https://www.theverge.com/rss/index.xml",
    "HackerNews":        "https://news.ycombinator.com/rss",
    "ProductHunt":       "https://www.producthunt.com/feed",
    "Reddit-Technology": "https://www.reddit.com/r/technology/.rss",
}

def get_rss_trends(keywords: list = None) -> pd.DataFrame:
    """
    Fetch latest headlines from RSS feeds.
    If keywords are provided, only return articles matching them.
    """
    keywords = [k.lower() for k in (keywords or [])]
    rows = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title     = entry.get("title", "")
                link      = entry.get("link", "")
                published = entry.get("published", "N/A")
                if keywords and not any(kw in title.lower() for kw in keywords):
                    continue
                rows.append({"source": source, "title": title[:80], "published": published, "url": link})
        except Exception:
            pass
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════
# 5. INSTAGRAM — hashtag info  (no official API)
# ══════════════════════════════════════════════
def get_instagram_hashtag_info(hashtag: str) -> dict:
    """
    Attempt to scrape basic public info for an Instagram hashtag page.
    Note: Instagram heavily restricts scraping; this may break with changes.
    For production use, consider the official Instagram Graph API.
    """
    url = f"https://www.instagram.com/explore/tags/{hashtag}/?__a=1&__d=dis"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data  = resp.json()
            count = (data.get("graphql", {})
                        .get("hashtag", {})
                        .get("edge_hashtag_to_media", {})
                        .get("count", "N/A"))
            return {"hashtag": f"#{hashtag}", "post_count": count, "status": "ok"}
        return {"hashtag": f"#{hashtag}", "post_count": "N/A", "status": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"hashtag": f"#{hashtag}", "post_count": "N/A", "status": str(e)}


# ══════════════════════════════════════════════
# DISPLAY HELPERS
# ══════════════════════════════════════════════
def print_df(title: str, df: pd.DataFrame, max_rows: int = 10) -> None:
    if df.empty:
        console.print(f"\n[yellow]{title}[/yellow]: no data\n")
        return
    table = Table(title=title, box=box.ROUNDED, show_lines=False, highlight=True)
    for col in df.columns:
        table.add_column(str(col), overflow="fold")
    for _, row in df.head(max_rows).iterrows():
        table.add_row(*[str(v) for v in row])
    console.print(table)


# ══════════════════════════════════════════════
# MAIN  — run all scouts
# ══════════════════════════════════════════════
def run_all(keywords: list = None, save_csv: bool = True):
    keywords = keywords or TRACK_KEYWORDS
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.rule(f"[bold cyan]Trend Scout Report — {ts}[/bold cyan]")

    # 1. Google trending searches
    console.print("\n[bold]1. Google — Today's Trending Searches[/bold]")
    g_trending = get_google_trending_searches()
    print_df("Google Trending Searches", g_trending)

    # 2. Google Trends interest for your keywords
    console.print("\n[bold]2. Google Trends — Keyword Interest (last 24h)[/bold]")
    g_interest = get_google_trends(keywords)
    print_df("Keyword Interest (0–100)", g_interest)

    # 3. Reddit rising posts
    console.print("\n[bold]3. Reddit — Rising Posts[/bold]")
    reddit_df = get_reddit_trends(limit=8)
    print_df("Reddit Rising", reddit_df)

    # 4. Twitter trends
    console.print("\n[bold]4. Twitter — Worldwide Trends[/bold]")
    tw_trends = get_twitter_trends(woeid=1)
    print_df("Twitter Trends", tw_trends)

    # 5. RSS latest headlines
    console.print("\n[bold]5. RSS Feeds — Latest Headlines[/bold]")
    rss_df = get_rss_trends(keywords=keywords)
    print_df("RSS Trends", rss_df)

    # 6. Instagram hashtags
    console.print("\n[bold]6. Instagram — Hashtag Post Counts[/bold]")
    ig_rows = [get_instagram_hashtag_info(kw) for kw in keywords]
    print_df("Instagram Hashtags", pd.DataFrame(ig_rows))

    # Save combined output
    if save_csv:
        combined = pd.concat(
            [df.assign(section=name) for name, df in [
                ("google_trending", g_trending),
                ("google_interest", g_interest),
                ("reddit",          reddit_df),
                ("twitter",         tw_trends),
                ("rss",             rss_df),
            ] if not df.empty],
            ignore_index=True,
        )
        fname = f"trends_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        combined.to_csv(fname, index=False)
        console.print(f"\n[green]Saved to {fname}[/green]")


# ══════════════════════════════════════════════
# USAGE EXAMPLES
# ══════════════════════════════════════════════
if __name__ == "__main__":
    # Run full report
    run_all(keywords=["AI", "bitcoin", "fashion", "startup"])

    # Or run individual scouts:
    # get_reddit_trends(limit=10)
    # get_google_trends(["python", "rust", "typescript"])
    # get_google_trending_searches("united_states")
    # get_twitter_trends(woeid=1)
    # get_rss_trends(keywords=["AI", "crypto"])
    # get_instagram_hashtag_info("streetwear")
    # search_twitter_keyword("ChatGPT", max_results=20)
