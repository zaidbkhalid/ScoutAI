"""
copy_playbook.py
----------------
What "good advertising" means in this project, written down once and
shared by every stage that produces marketing output.

WHY THIS EXISTS:
    The model already knows the classic copywriting principles -- it has
    read far more ads and copywriting books than any of us. What it
    doesn't do unprompted is APPLY them: left alone it drifts to safe,
    generic, adjective-heavy copy ("Indulge in our delicious treats!").
    This file names the principles explicitly and gives a checklist the
    editor pass (ad_copy.py) scores drafts against.

    These are distilled principles in our own words -- the ideas of
    practitioners like David Ogilvy (specificity, research, the reader
    as an intelligent person), Eugene Schwartz (match the message to how
    aware the reader is), Joseph Sugarman (every line exists to get the
    next line read) and modern short-video practice (hook in the first
    two seconds). No real ads are reproduced here.

HOW IT'S USED:
    - CAMPAIGN_PRINCIPLES  -> campaign_ideas.py system prompt
    - COPY_PRINCIPLES      -> ad_copy.py system prompt (first draft)
    - EDITOR_CHECKLIST     -> ad_copy.py editor pass (score, then rewrite)
    - BANNED_PHRASES       -> both prompts, and a code-level check
"""

import re

CAMPAIGN_PRINCIPLES = """WHAT MAKES A CAMPAIGN IDEA GOOD:
1. One big idea, sayable in a single sentence. If it needs "and" twice, it's two ideas.
2. Built on a human insight, not on the trend's existence. "People are watching the match"
   is a fact; "match nights are social, and nobody wants to be the host who forgot snacks"
   is an insight a business can act on.
3. The trend is the moment, the business is the answer. The link should feel natural to
   someone who has never heard of the business -- if you have to explain it, it's forced.
4. Specific to THIS business: its actual products, its city, its audience, its tone. A
   campaign that would work unchanged for any bakery is too generic.
5. Doable by a small team this week: no celebrity partnerships, no big production, no
   budget the profile doesn't suggest. Prefer what one person with a phone can run.
6. Has a reason to act now, and that reason comes from the trend's timing -- not from an
   invented discount.
7. Never exploits tragedy, conflict or anything a reasonable customer would find tasteless.
8. Never build on things the business hasn't said it has: discounts, free trials, giveaways,
   testimonials, reviews, partnerships or awards. If an offer would genuinely help, label it
   as a suggestion for the owner ("Optional: a small match-night bundle price") -- the copy
   stage will leave it as a placeholder until the owner confirms it.
"""

COPY_PRINCIPLES = """HOW GREAT ADVERTISING COPY WORKS (apply all of this):

Hook first.
- The first line decides whether anything else is read. Lead with the most interesting,
  specific or surprising thing -- a concrete moment, a question the reader is already
  asking, a sharp contrast. Never open with the brand name, a greeting ("Hey there!"),
  or a generic exclamation ("Exciting news!").

Specific beats clever, and clever beats generic.
- Concrete nouns and numbers that were provided ("a 6-inch Lotus Biscoff cake, ready in
  48 hours") outperform adjectives ("delicious, high-quality cakes"). If a detail is in
  the profile, use it; if it isn't, don't invent it -- leave a [PLACEHOLDER].
- Write about the reader's moment, not the business's qualities. Benefits and feelings
  over features: what changes for them?

Meet the reader where they are.
- Trend-led marketing reaches people who weren't thinking about this business. So start
  from the trend moment they ARE thinking about, then bridge to the product. Don't open
  with a sales pitch to someone who doesn't know you yet.

Sound like a person.
- Write how this business would actually talk to a customer, in its stated tone. Short
  sentences. Contractions. Read it aloud -- if it sounds like an ad, rewrite it.
- Local and cultural references the audience shares are powerful; use them only where the
  profile supports them, and never in a way that stereotypes.

One message, one action.
- Every piece makes one point and asks for one specific, easy action ("DM us 'MATCH' to
  order by Friday"), not several vague ones ("follow, like, share and visit!").

Native to the platform.
- A TikTok script is something a person says on camera, not a TV ad. A WhatsApp message
  reads like it's from a person. A search ad matches what someone typed. Respect every
  character limit exactly -- over-limit copy is unusable.

Restraint.
- Emojis: at most two or three, only where they add meaning. Hashtags only where the
  platform uses them. No ALL CAPS shouting, no stacked exclamation marks.
"""

# Original illustrations of the gap between generic and specific copy.
# Deliberately about businesses that are NOT the user's -- they teach the
# move, not the words. Placeholders mark facts the writer must not invent.
COPY_EXAMPLES = """EXAMPLES -- the move from generic to specific (learn the pattern, never reuse the words
or the facts; the placeholders show where real details from the profile would go):

1. Opening line, neighbourhood cafe + first rain of the season
   Generic:  "Beat the rain with our delicious drinks! ☔"
   Specific: "First rain of the year and the whole street just went quiet. Kettle's on till [time]."
   Why: starts inside the reader's actual moment instead of shouting at them.

2. Product claim, neighbourhood tailor before Eid
   Generic:  "Quality tailoring at affordable prices!"
   Specific: "Eid is [N] days away and our stitching queue fills up first. Book your fitting this week."
   Why: a real deadline from the trend, a concrete action, no empty adjectives.

3. Call to action, any local shop
   Generic:  "Follow us, like, share and visit our page!"
   Specific: "Reply 'MENU' and we'll send this week's options."
   Why: one easy action the reader can take in five seconds.

4. WhatsApp message, small boutique
   Generic:  "Hi! 🌞 Check out our amazing new collection!"
   Specific: "Hi [name], it's [owner] from [shop]. The linen sets you asked about are back -- want me to keep one aside?"
   Why: sounds like a person who knows the customer, not a brand broadcast.

5. Search ad headline, home cleaning service
   Generic:  "Best Cleaning Services Here"
   Specific: "Deep Cleaning in [City] This Week"
   Why: mirrors what the person typed and when they need it.

6. B2B, software for clinics
   Generic:  "Streamline your workflow with our powerful platform!"
   Specific: "Flu season doubles the phone calls. Let patients book themselves while you see the ones in front of you."
   Why: names the pain the trend creates, then the relief.

Never reuse an example's wording or sentence structure -- every campaign deserves its own
opening, built from its own trend and business.
"""

BANNED_PHRASES = [
    "elevate", "unlock", "unleash", "indulge in", "look no further", "game-changer",
    "game changer", "next level", "in today's fast-paced world", "delve", "embark",
    "treat yourself", "something for everyone", "we've got you covered",
    "hey there", "exciting news", "don't miss out", "you won't want to miss",
    "taste the difference", "experience the magic", "the wait is over",
    "say goodbye to", "revolutionize", "seamless", "a symphony of",
]

EDITOR_CHECKLIST = [
    ("hook",        "The first line earns attention: specific, surprising or a question the reader already has. No greeting, no brand-first opening."),
    ("specific",    "Uses concrete details from the profile (products, place, audience) instead of adjectives. Nothing invented."),
    ("voice",       "Sounds like this business talking to a real customer, in its stated tone. Would pass the read-aloud test."),
    ("trend_link",  "The trend tie-in feels natural and tasteful, not bolted on."),
    ("one_action",  "One clear, specific, easy call to action."),
    ("native",      "Fits the platform's style and every stated limit (characters, length, structure)."),
    ("fresh",       "Free of clichés and stock ad phrases; no emoji or hashtag overload."),
    ("honest",      "No invented prices, dates, discounts, stats, reviews or guarantees; placeholders used instead."),
]


def checklist_text():
    return "\n".join(f"- {key}: {desc}" for key, desc in EDITOR_CHECKLIST)


def banned_list_text():
    return ", ".join(f'"{p}"' for p in BANNED_PHRASES)


# Offer language the writer may only use if the brief itself contains it.
OFFER_PATTERNS = [
    r"\d+\s?%(\s?off)?", r"\bdiscount\w*", r"\bfree (delivery|shipping|gift|sample|trial)\b",
    r"\bbuy one\b", r"\bbogo\b", r"\bon sale\b", r"\bsale\b", r"\blimited[- ]time offer\b",
    r"\bcoupon\b", r"\bpromo code\b", r"\bfree for\b", r"\bfree (month|week|trial|class|session)\b",
    r"\b(one|1|first|a) (month|week)s? free\b", r"\bfree tier\b",
]


def find_invented_offers(text, brief):
    """Offer claims in `text` that the brief (profile + campaign) never makes."""
    brief = (brief or "").lower()
    found = []
    for pat in OFFER_PATTERNS:
        for m in re.finditer(pat, text or "", re.IGNORECASE):
            word = m.group(0).lower()
            if word not in brief and word not in found:
                found.append(word)
    return found


def find_banned(text):
    """Banned phrases present in text (case-insensitive, whole words)."""
    found = []
    for p in BANNED_PHRASES:
        if re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", text or "", re.IGNORECASE):
            found.append(p)
    return found
