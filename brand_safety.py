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
    campaign material. It only removes death, violence, crime, disaster,
    and adult content.
"""

import re

# Matched case-insensitively against the trend text as whole words.
UNSAFE_PATTERNS = [
    # Death and injury
    r"\b(dead|death|deaths|died|dies|dying|fatal|fatally|killed|kills|killing)\b",
    r"\b(casualt(y|ies)|corpse|bodies found|body found|toll)\b",
    r"\b(suicide|overdose|massacre|genocide)\b",
    # Violence and crime
    r"\b(murder\w*|homicide|stabbed|stabbing|shooting|shot dead|gunman)\b",
    # Wikipedia's most-viewed list surfaces people by what they did
    # ("American murderer", "on death row"), not by an event verb.
    r"\b(serial killer|death row|crimes?|criminal|sex offender|sex crimes?)\b",
    r"\b(rape|raped|assault|assaulted|molest\w*|abuse[ds]?|harassment)\b",
    r"\b(attack|attacked|attacks|attacker|terror\w*|bomb|bombing|blast|explosion)\b",
    r"\b(kidnap\w*|hostage|abduct\w*|trafficking)\b",
    r"\b(arrest\w*|jailed|imprisoned|convicted|indicted|fraud|scam|bribery)\b",
    # Conflict
    r"\b(war|warfare|missile|airstrike|troops|invasion|militant\w*|insurgen\w*)\b",
    r"\b(riot|riots|unrest|clashes?|violence|violent)\b",
    # South Asian news phrasing the words above miss ("2 civilians
    # martyred in firing by ... forces"). "firing" alone is left out --
    # it also means kilns and grills -- so only its conflict forms match.
    r"\b(martyr\w*|gunfire|shelling|ceasefire|cross-border firing)\b",
    r"\bfiring (by|on|at|from)\b",
    # Disaster
    r"\b(crash|crashes|crashed|derail\w*|collapse[ds]?|wreck\w*)\b",
    r"\b(earthquake|flood|floods|flooding|wildfire|tsunami|famine|drought)\b",
    r"\b(outbreak|epidemic|pandemic|disaster|catastroph\w*|emergency)\b",
    r"\bfire (rips|breaks|guts|destroy\w*)\b",
    r"\b(rips through|burnt alive|burned alive)\b",
    # Adult content -- Reddit and Wikipedia's most-viewed lists carry it.
    r"\b(porn\w*|xxx|nsfw|onlyfans|nudes?|sex tape|erotic\w*)\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in UNSAFE_PATTERNS]

# Extra filters for specific markets, applied on top of the universal
# list when the fetch is for that country. Global feeds (r/food, Bon
# Appetit) happily surface pork recipes and cocktail trends; in a
# Muslim-majority market those are not campaign material for a typical
# small business, and some (alcohol, gambling) are restricted outright.
_MUSLIM_MAJORITY = [
    r"\b(pork|bacon|ham|hams|prosciutto|pancetta|pepperoni|salami|chorizo|lard|carnitas|chicharr[oó]n\w*)\b",
    r"\b(alcohol\w*|beer|beers|wine|wines|whisk(e)?y|vodka|tequila|gin|rum|liquor|cocktails?|brewer(y|ies)|champagne|margaritas?|sangria|booze|drunk)\b",
    r"\b(casino|gambling|betting|bets|sportsbook|poker)\b",
]
REGIONAL_PATTERNS = {
    "PK": _MUSLIM_MAJORITY,
    "AE": _MUSLIM_MAJORITY,
}
_REGIONAL_COMPILED = {
    region: [re.compile(p, re.IGNORECASE) for p in patterns]
    for region, patterns in REGIONAL_PATTERNS.items()
}


def _patterns_for(region):
    return _COMPILED + _REGIONAL_COMPILED.get((region or "").upper(), [])


def is_brand_safe(*texts, region=None):
    """True when none of the given strings look campaign-unsafe.

    `region` (a country code) adds that market's own filters, if any.
    """
    blob = " ".join(t for t in texts if t)
    if not blob.strip():
        return True
    return not any(rx.search(blob) for rx in _patterns_for(region))


def first_match(*texts, region=None):
    """Which pattern tripped, for logging. None when safe."""
    blob = " ".join(t for t in texts if t)
    for rx in _patterns_for(region):
        m = rx.search(blob)
        if m:
            return m.group(0)
    return None
