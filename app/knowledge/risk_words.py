# app/knowledge/risk_words.py
#
# Loads risk_words.txt from the SharePoint knowledge folder at agent
# startup and parses it into in-memory structures. This file is NOT
# chunked or embedded like other knowledge documents — it is loaded
# whole and held in memory for the lifetime of the process, because
# risk words must be checked against EVERY generated output without
# exception.
#
# CORRECTED based on the real file content: this is a JSON file, not
# a flat line-per-word text file. Real structure:
#   {
#     "blocked": ["ensure", "guarantee", "trusted advisor", ...],
#     "suggestions": {"ensure": "help achieve", "guarantee": "aim to
#                      deliver", ...}
#   }
# Despite the .txt extension, the content is JSON — parsed with
# json.loads(), not line-splitting.
#
# Matching is substring-based, including for multi-word phrases
# ("trusted advisor", "no surprises"). KNOWN LIMITATION, accepted
# deliberately: several blocked entries are short common English
# words (review, audit, trust, best) that will also match as
# substrings inside unrelated words (reviewed, interview, robust,
# bestow). This means contains_risk_word() can over-flag — false
# positives are the accepted failure mode here, not false negatives,
# which is the correct trade-off for a compliance guardrail (better
# to flag something innocuous for human review than to silently miss
# real violation). See get_blocked_matches() docstring for detail.

import json
import logging
from typing import Optional

from app.knowledge.graph_client import graph_client

logger = logging.getLogger("app.knowledge.risk_words")

RISK_WORDS_FILENAME = "risk_words.txt"


class RiskWordsData:
    """Holds the parsed blocked-word list and suggestion map."""

    def __init__(self, blocked: list[str], suggestions: dict[str, str]):
        self.blocked = [w.lower() for w in blocked]
        self.suggestions = {k.lower(): v for k, v in suggestions.items()}


# Module-level cache — populated once at startup via load_risk_words(),
# read many times per request during draft generation/validation.
# A plain object, not reloaded per request — risk words changing
# requires a deliberate re-sync, not silently picked up mid-process.
_risk_words: Optional[RiskWordsData] = None


class RiskWordsNotLoadedError(Exception):
    """
    Raised if get_risk_words() is called before load_risk_words() has
    successfully run. This should only happen if startup ordering is
    wrong — surfacing this loudly is correct, since silently
    proceeding with no loaded risk words would mean generation and
    validation run with NO compliance guardrail at all.
    """
    pass


def _parse_risk_words(raw_text: str) -> RiskWordsData:
    """
    Parses the real risk_words.txt structure: a JSON object with a
    "blocked" list and a "suggestions" map. Validates the expected
    keys are present rather than assuming silently — a malformed or
    unexpectedly-shaped file should fail loudly at startup, not
    produce an empty or partial guardrail list without anyone
    noticing.
    """
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"risk_words.txt is not valid JSON: {e}. "
            f"Expected a JSON object with 'blocked' and 'suggestions' keys."
        ) from e

    if "blocked" not in data or not isinstance(data["blocked"], list):
        raise ValueError(
            "risk_words.txt is missing a 'blocked' list, or it is not "
            "a list. Refusing to proceed with an incomplete guardrail."
        )

    suggestions = data.get("suggestions", {})
    if not isinstance(suggestions, dict):
        logger.warning(
            "risk_words.txt 'suggestions' field is present but not a "
            "dict — ignoring it. Blocked-word list itself is unaffected."
        )
        suggestions = {}

    return RiskWordsData(blocked=data["blocked"], suggestions=suggestions)


async def load_risk_words() -> RiskWordsData:
    """
    Finds risk_words.txt in the configured SharePoint knowledge
    folder, downloads it, parses it, and caches the result in memory.

    Called once at app startup (see app/main.py lifespan). Raises
    clearly if the file cannot be found, downloaded, or parsed — an
    agent that starts up successfully but silently has zero risk
    words loaded would be a serious, invisible compliance gap, so
    this fails loudly rather than degrading gracefully.
    """
    global _risk_words

    logger.info("Loading risk_words.txt from SharePoint knowledge folder...")

    item = await _find_risk_words_item()
    if item is None:
        raise RuntimeError(
            f"Could not find '{RISK_WORDS_FILENAME}' in the configured "
            f"SharePoint knowledge folder. Risk word validation cannot "
            f"proceed without this file — refusing to start with an "
            f"empty guardrail list."
        )

    file_bytes = await graph_client.download_file(item["id"])
    if file_bytes is None:
        raise RuntimeError(
            f"Found '{RISK_WORDS_FILENAME}' but failed to download "
            f"its content."
        )

    raw_text = file_bytes.decode("utf-8", errors="replace")
    _risk_words = _parse_risk_words(raw_text)

    logger.info(
        "Loaded %d blocked word(s)/phrase(s), %d suggestion(s)",
        len(_risk_words.blocked), len(_risk_words.suggestions),
    )
    return _risk_words


async def _find_risk_words_item() -> Optional[dict]:
    """
    Performs a full (non-delta) scan of the knowledge folder looking
    for risk_words.txt by exact filename match. A full scan is used
    here rather than delta, since this runs once at startup before
    any delta_link exists yet, and we only need to find one specific
    file, not enumerate everything for indexing purposes.
    """
    async for item in graph_client.iter_changes(delta_link=None):
        if item.get("folder") is not None:
            continue
        if not graph_client.is_in_knowledge_folder(item):
            continue
        if item.get("name", "").lower() == RISK_WORDS_FILENAME.lower():
            return item
    return None


def get_risk_words() -> RiskWordsData:
    """
    Returns the cached risk word data. Used by the assemble_prompt
    and validate_output nodes — called many times per conversation
    turn, so this is a cheap in-memory read, never a network call.
    """
    if _risk_words is None:
        raise RiskWordsNotLoadedError(
            "Risk words have not been loaded yet. "
            "load_risk_words() must be called during app startup "
            "before this function can be used."
        )
    return _risk_words


def get_blocked_matches(text: str) -> list[tuple[str, Optional[str]]]:
    """
    Returns every blocked word/phrase found in the given text, paired
    with its suggested replacement if one exists (None otherwise).

    Matching is case-insensitive substring matching, including for
    multi-word phrases. KNOWN LIMITATION: several blocked entries are
    short, common English words ("review", "audit", "trust", "best")
    that will also match inside unrelated words ("reviewed",
    "interview", "robust", "bestow") — this can over-flag. This is
    accepted deliberately: for a compliance guardrail, false positives
    (flagging something innocuous for human review) are a far safer
    failure mode than false negatives (silently missing a real
    violation). Tightening this to word-boundary-aware matching is a
    reasonable future improvement if over-flagging proves disruptive
    in practice — not done now since it has not yet been tested
    against real generated drafts.
    """
    data = get_risk_words()
    lowered = text.lower()
    matches = []
    for word in data.blocked:
        if word in lowered:
            matches.append((word, data.suggestions.get(word)))
    return matches