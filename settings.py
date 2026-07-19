"""User preferences, persisted in the database.

Chosen on the start screen: which assets to follow, how much virtual capital to
begin with, and the interface language. Stored as one JSON blob in `meta` so
the shape can evolve without schema migrations.
"""

from __future__ import annotations

import json
from typing import Any

import config
import db

KEY = "settings"

DEFAULTS: dict[str, Any] = {
    "initialized": False,
    "language": "en",
    "starting_capital": config.STARTING_CAPITAL,
    "followed": list(config.SYMBOLS),
}


def get() -> dict[str, Any]:
    raw = db.get_meta(KEY)
    if not raw:
        return dict(DEFAULTS)
    try:
        stored = json.loads(raw)
    except ValueError:
        return dict(DEFAULTS)
    out = dict(DEFAULTS)
    out.update({k: v for k, v in stored.items() if k in DEFAULTS})
    # Drop symbols that have left the registry rather than serving ghosts.
    out["followed"] = [s for s in out["followed"] if s in config.BY_SYMBOL] or list(config.SYMBOLS)
    return out


def save(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist a partial update, returning the merged settings."""
    current = get()

    if "language" in updates:
        lang = str(updates["language"])
        if lang not in config.SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language: {lang}")
        current["language"] = lang

    if "followed" in updates:
        followed = [str(s).upper() for s in updates["followed"]]
        unknown = [s for s in followed if s not in config.BY_SYMBOL]
        if unknown:
            raise ValueError(f"Unknown symbols: {', '.join(unknown)}")
        if not followed:
            raise ValueError("Select at least one asset to follow.")
        # Preserve registry order regardless of what the client sent.
        current["followed"] = [s for s in config.SYMBOLS if s in set(followed)]

    if "starting_capital" in updates:
        try:
            capital = float(updates["starting_capital"])
        except (TypeError, ValueError):
            raise ValueError("Starting capital must be a number.") from None
        if not config.CAPITAL_MIN <= capital <= config.CAPITAL_MAX:
            raise ValueError(
                f"Starting capital must be between ${config.CAPITAL_MIN:,.0f} "
                f"and ${config.CAPITAL_MAX:,.0f}."
            )
        current["starting_capital"] = capital

    current["initialized"] = True
    db.set_meta(KEY, json.dumps(current))
    return current
