"""User preferences, persisted in the **durable** store.

Chosen on the start screen: which assets to follow, how much virtual capital to
begin with, and the interface language. Stored as one JSON blob under a single
key in `userstore.app_meta`, so the shape can evolve without schema migrations.

These used to live in `db.meta`, on the ephemeral market-data store, and that
was wrong in a way that cost players their portfolios. That disk is wiped on
every restart and idle spin-down, so `starting_capital` reverted to the $100k
default -- silently reseeding every new visitor at a number nobody chose -- and
`initialized` reverted to False, which used to make the next trip through the
start screen reset that player's portfolio. Configuration is not regenerable
the way candles are; it belongs with the accounts it configures.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import config
import userstore

log = logging.getLogger("settings")

KEY = "settings"

DEFAULTS: dict[str, Any] = {
    "initialized": False,
    "language": "en",
    "starting_capital": config.STARTING_CAPITAL,
    "followed": list(config.SYMBOLS),
}


def get() -> dict[str, Any]:
    raw = userstore.get_meta(KEY)
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
    userstore.set_meta(KEY, json.dumps(current))
    return current


def adopt_legacy() -> None:
    """Carry a pre-existing blob over from the ephemeral store, once.

    Settings used to live in `db.meta`. On Render this is a no-op -- the deploy
    that ships this change wipes the ephemeral disk, so there is nothing left to
    find -- but a local checkout has a configured `db.meta` that would otherwise
    revert to the defaults exactly once, which is the bug this whole change
    exists to remove.

    A one-shot at startup rather than a fallback inside get(): get() is called
    on every request through require_user, plus /api/overview, /api/trade and
    every engine cycle, and a read-path fallback would add a permanent
    ephemeral-store hit to serve a condition that is true for at most one
    deployment. Safe to delete once every deployment has booted on this code.
    """
    if userstore.get_meta(KEY):
        return
    import db          # local: the durable path must not depend on this module
    raw = db.get_meta(KEY)
    if raw:
        userstore.set_meta(KEY, raw)
        log.info("settings: carried over from the ephemeral store")
