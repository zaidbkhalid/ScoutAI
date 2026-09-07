"""
brand_safety.py
---------------
Filters out trends no small business should be building a marketing
campaign on top of.

WHY THIS EXISTS:
    The trend sources are general-purpose news and search feeds. A live
    pull will happily surface "factory fire kills 3", "acid attack bill
    passes senate", or "cargo plane crashes" alongside "pistachio kunafa"
    and "women's asia cup". Those are real trends -- they are simply not
    things to hang a promotion on, and surfacing them in a list captioned
    "turn these into campaign ideas" is the kind of suggestion that gets a
    brand in trouble.

WHERE IT APPLIES:
    Snapshots stay unfiltered -- they are a raw record of what was
    observed, and that record should not quietly lie. The filter is
    applied at scoring time, when the data stops being an observation and
    starts being a recommendation.

WHAT IT DOES NOT DO:
    This is a keyword heuristic, not comprehension. It will miss things
    and it will occasionally drop something harmless (an article about a
    "killer" playlist). It deliberately does NOT filter politics,
    economics, or general news -- a fuel price hike is legitimately
    useful to a delivery business, and national days are legitimate
    campaign material. It only removes death, violence, crime, and
    disaster.
"""

import re

# Matched case-insensitively against the trend text as whole words.
UNSAFE_PATTERNS = [
    # Death and injury
    r"\b(dead|death|deaths|died|dies|dying|fatal|fatally|killed|kills|killing)\b",
    r"\b(casualt(y|ies)|corpse|bodies found|body found|toll)\b",
    r"\b(suicide|overdose|massacre|genocide)\b",
    # Violence and crime
    r"\b(murder|murdered|homicide|stabbed|stabbing|shooting|shot dead|gunman)\b",
    r"\b(rape|raped|assault|assaulted|molest\w*|abuse[ds]?|harassment)\b",
    r"\b(attack|attacked|attacks|attacker|terror\w*|bomb|bombing|blast|explosion)\b",
    r"\b(kidnap\w*|hostage|abduct\w*|trafficking)\b",
    r"\b(arrest\w*|jailed|imprisoned|convicted|indicted|fraud|scam|bribery)\b",
    # Conflict
    r"\b(war|warfare|missile|airstrike|troops|invasion|militant\w*|insurgen\w*)\b",
    r"\b(riot|riots|unrest|clashes?|violence|violent)\b",
    # Disaster
    r"\b(crash|crashes|crashed|derail\w*|collapse[ds]?|wreck\w*)\b",
    r"\b(earthquake|flood|floods|flooding|wildfire|tsunami|famine|drought)\b",
    r"\b(outbreak|epidemic|pandemic|disaster|catastroph\w*|emergency)\b",
    r"\bfire (rips|breaks|guts|destroy\w*)\b",
    r"\b(rips through|burnt alive|burned alive)\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in UNSAFE_PATTERNS]


def is_brand_safe(*texts):
    """True when none of the given strings look campaign-unsafe."""
    blob = " ".join(t for t in texts if t)
    if not blob.strip():
        return True
    return not any(rx.search(blob) for rx in _COMPILED)


def first_match(*texts):
    """Which pattern tripped, for logging. None when safe."""
    blob = " ".join(t for t in texts if t)
    for rx in _COMPILED:
        m = rx.search(blob)
        if m:
            return m.group(0)
    return None
