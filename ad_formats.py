"""
ad_formats.py
-------------
The catalogue of ad-copy formats Stage 3 can write.

Single source of truth, deliberately: ad_copy.py builds its prompt from
`guidance`, app.py serves the list to the web UI, and the picker renders
from that same list. Adding a format is one edit here, not three.

`guidance` is written for the model, not the user -- it carries the
practical constraints that actually make output usable (character
limits on search ads, the shape of a short-video script, where a
subject line goes) instead of leaving the model to guess.
"""

FORMATS = [
    # ── Social posts ────────────────────────────────────────────
    {
        "key": "instagram_post",
        "label": "Instagram post",
        "group": "Social posts",
        "hint": "Feed caption with hashtags",
        "guidance": (
            "An Instagram feed caption. Open with a scroll-stopping first line "
            "(only ~125 characters show before 'more'). Conversational, line "
            "breaks for readability, one clear call to action. Supply 8-15 "
            "relevant hashtags mixing broad and niche."
        ),
    },
    {
        "key": "instagram_story",
        "label": "Instagram Story",
        "group": "Social posts",
        "hint": "Short, punchy, one idea per frame",
        "guidance": (
            "A 3-5 frame Instagram Story sequence. Each frame is one short "
            "line of on-screen text (under ~12 words) plus a note on what to "
            "show. Use script_beats, one beat per frame. Suggest a sticker or "
            "interaction (poll, question, link) where it fits."
        ),
    },
    {
        "key": "facebook_post",
        "label": "Facebook post",
        "group": "Social posts",
        "hint": "Longer-form, community tone",
        "guidance": (
            "A Facebook post. Can run longer and more narrative than "
            "Instagram; Facebook audiences skew toward detail and local "
            "community framing. Plain-spoken, minimal hashtags (2-4 max)."
        ),
    },
    {
        "key": "linkedin_post",
        "label": "LinkedIn post",
        "group": "Social posts",
        "hint": "B2B, professional framing",
        "guidance": (
            "A LinkedIn post. Lead with an insight or observation, not a "
            "promotion. Short paragraphs, professional but not stiff, no "
            "hype. Only relevant for B2B or founder-voice content -- if this "
            "business has no plausible LinkedIn audience, say so in notes "
            "rather than forcing it."
        ),
    },
    {
        "key": "x_post",
        "label": "X / Twitter post",
        "group": "Social posts",
        "hint": "Single post or short thread",
        "guidance": (
            "An X/Twitter post under 280 characters, or a 3-5 post thread if "
            "the idea needs it (use variants for thread parts). Punchy, no "
            "hashtag stuffing (0-2 max)."
        ),
    },
    {
        "key": "pinterest_pin",
        "label": "Pinterest pin",
        "group": "Social posts",
        "hint": "Search-driven, descriptive",
        "guidance": (
            "A Pinterest pin title (under 100 characters) and description "
            "(under 500). Pinterest is a search engine -- write descriptively "
            "with natural keywords people would actually search."
        ),
    },
    {
        "key": "google_business_post",
        "label": "Google Business Profile post",
        "group": "Social posts",
        "hint": "Shows in local search / Maps",
        "guidance": (
            "A Google Business Profile update (under ~1500 characters, but "
            "shorter performs better). Factual and local-intent: what's on "
            "offer, when, where. Include one CTA button suggestion (Order, "
            "Book, Learn more, Call)."
        ),
    },

    # ── Short video scripts ─────────────────────────────────────
    {
        "key": "tiktok_script",
        "label": "TikTok video script",
        "group": "Short video scripts",
        "hint": "Hook-first, 15-45 seconds",
        "guidance": (
            "A TikTok script of 15-45 seconds. Use script_beats with "
            "timestamps. The first 2 seconds decide everything -- open on the "
            "hook, never on a logo or greeting. Give on-screen text AND what's "
            "happening on camera per beat. Native and unpolished beats "
            "advertising-slick. Suggest a sound/trend direction in notes."
        ),
    },
    {
        "key": "instagram_reel_script",
        "label": "Instagram Reel script",
        "group": "Short video scripts",
        "hint": "15-60 seconds, aesthetic-led",
        "guidance": (
            "An Instagram Reel script, 15-60 seconds, in script_beats with "
            "timestamps. Reels skew more polished and aesthetic than TikTok. "
            "Include on-screen text per beat plus a caption in primary_text."
        ),
    },
    {
        "key": "youtube_shorts_script",
        "label": "YouTube Shorts script",
        "group": "Short video scripts",
        "hint": "Up to 60 seconds, retention-led",
        "guidance": (
            "A YouTube Shorts script up to 60 seconds in script_beats. "
            "YouTube rewards retention and searchability: strong hook, a "
            "reason to stay to the end, and a searchable title in headline."
        ),
    },

    # ── Direct messaging ────────────────────────────────────────
    {
        "key": "email_campaign",
        "label": "Email campaign",
        "group": "Direct messaging",
        "hint": "Subject lines + full body",
        "guidance": (
            "A marketing email. Give 3 subject line options in variants "
            "(under ~50 characters each), a preview/preheader line in "
            "headline, and the full body in primary_text with a clear single "
            "call to action. Scannable, short paragraphs."
        ),
    },
    {
        "key": "whatsapp_broadcast",
        "label": "WhatsApp broadcast",
        "group": "Direct messaging",
        "hint": "Short, personal, high open rate",
        "guidance": (
            "A WhatsApp broadcast message. Short (under ~60 words), personal, "
            "written like a message from a person not a brand. One clear "
            "action. No formatting beyond an emoji or two. Note in notes that "
            "this should only go to people who opted in."
        ),
    },
    {
        "key": "sms",
        "label": "SMS / text message",
        "group": "Direct messaging",
        "hint": "Under 160 characters",
        "guidance": (
            "An SMS under 160 characters including the CTA. Extremely direct. "
            "Note in notes that recipients must have opted in and that an "
            "opt-out instruction is normally expected."
        ),
    },

    # ── Paid ads ────────────────────────────────────────────────
    {
        "key": "meta_ad",
        "label": "Facebook / Instagram ad",
        "group": "Paid ads",
        "hint": "Paid Meta ad copy",
        "guidance": (
            "A Meta (Facebook/Instagram) paid ad. Give primary_text (the body, "
            "keep the key message in the first ~125 characters before the "
            "'See more' cut), headline (under 40 characters), and a short "
            "description (under 30). Offer 2-3 primary_text variants for "
            "testing."
        ),
    },
    {
        "key": "google_search_ad",
        "label": "Google Search ad",
        "group": "Paid ads",
        "hint": "Strict character limits",
        "guidance": (
            "A Google Search responsive ad. Character limits are hard: give "
            "at least 5 headlines of MAX 30 characters each in variants, and "
            "2 descriptions of MAX 90 characters each. Count characters "
            "carefully -- over-limit copy is unusable. Match search intent, "
            "not brand voice."
        ),
    },
    {
        "key": "youtube_preroll_script",
        "label": "YouTube pre-roll ad script",
        "group": "Paid ads",
        "hint": "Skippable, first 5s critical",
        "guidance": (
            "A skippable YouTube pre-roll script in script_beats. The first 5 "
            "seconds must land the value before the skip button -- treat "
            "everything after as a bonus. 15-30 seconds total."
        ),
    },

    # ── Longer form ─────────────────────────────────────────────
    {
        "key": "blog_outline",
        "label": "Blog post / SEO article",
        "group": "Longer form",
        "hint": "Outline with headings",
        "guidance": (
            "An SEO-minded blog outline: a working title in headline, a "
            "one-paragraph intro angle in primary_text, and section headings "
            "with a line on what each covers in script_beats. Note the search "
            "intent it targets."
        ),
    },
    {
        "key": "landing_page_copy",
        "label": "Landing page copy",
        "group": "Longer form",
        "hint": "Hero, benefits, CTA",
        "guidance": (
            "Landing page copy: a hero headline (under 12 words) in headline, "
            "a supporting subhead and body in primary_text, 3 benefit bullets "
            "in variants, and one primary call to action."
        ),
    },
    {
        "key": "influencer_brief",
        "label": "Influencer / UGC brief",
        "group": "Longer form",
        "hint": "What to ask a creator for",
        "guidance": (
            "A brief to hand a creator: the angle, what must be shown or "
            "said, what to avoid, and deliverables. This is instructions FOR "
            "a creator, not copy to publish. Disclosure matters more than "
            "usual here -- address it in disclosure_note."
        ),
    },
    {
        "key": "flyer_poster",
        "label": "Flyer / poster copy",
        "group": "Longer form",
        "hint": "Print, in-store, physical",
        "guidance": (
            "Copy for a physical flyer or poster: a big headline readable "
            "from a distance, a short supporting line, the essential details "
            "(what/when/where/price), and a CTA. Minimal words -- this is read "
            "in passing."
        ),
    },
]

FORMATS_BY_KEY = {f["key"]: f for f in FORMATS}

# Preserves the order groups appear in FORMATS.
GROUP_ORDER = list(dict.fromkeys(f["group"] for f in FORMATS))


def resolve(keys):
    """Valid format dicts for the given keys, in catalogue order."""
    wanted = set(keys or [])
    return [f for f in FORMATS if f["key"] in wanted]


def grouped():
    """[{group, formats:[...]}] for rendering a grouped picker."""
    return [
        {
            "group": g,
            "formats": [
                {k: f[k] for k in ("key", "label", "hint")}
                for f in FORMATS if f["group"] == g
            ],
        }
        for g in GROUP_ORDER
    ]
