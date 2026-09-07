"""
demo_mode.py
------------
THIS IS TEMPORARY SCAFFOLDING, meant to be reversed once a real API key
is funded. It lets the full product (main trend pipeline + competitor
research + AI legibility check) run end-to-end with zero OpenAI/
Perplexity spend, by swapping in hand-written canned output for the 3
built-in synthetic example businesses (Velvet Crumbs, FirePit BBQ Co.,
Glowly Skincare) wherever a script would otherwise call a paid API.

Enable with DEMO_MODE=1 in .env or the environment.

How it's used: every script that calls OpenAI or Perplexity checks
is_demo_mode() + resolve_preset_key(...) right before making the real
API call. If both match, it loads the canned response from mock_data/
and returns early -- the real API call code sits right below, unchanged
and untouched. Any business that isn't one of the 3 presets falls
through to the real API call exactly as before, even with DEMO_MODE=1.

To reverse this later: delete this file, mock_data/, and the small
"if is_demo_mode(): ..." blocks each script has near its API call --
each one is self-contained and clearly marked.
"""

import json
import os
from pathlib import Path

MOCK_DATA_DIR = Path(__file__).parent / "mock_data"

# Business name (lowercased, substring match) -> preset folder name.
PRESET_KEYS = {
    "velvet crumbs": "velvet",
    "firepit bbq": "firepit",
    "glowly skincare": "glowly",
}


def is_demo_mode():
    return os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")


def resolve_preset_key(text):
    """Match a business name (or a whole business .txt's raw text) to one
    of the 3 built-in presets. Returns the preset key, or None."""
    haystack = (text or "").lower()
    for name, key in PRESET_KEYS.items():
        if name in haystack:
            return key
    return None


def load_mock(preset_key, filename):
    """Load a canned JSON file for a preset. Returns None if missing."""
    if not preset_key:
        return None
    path = MOCK_DATA_DIR / preset_key / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
