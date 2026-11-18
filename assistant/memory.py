"""Small explicit grammars for memory commands; documents and tool output never reach them.

Only an unambiguous, durable, first-person statement by the operator becomes a stored
preference. Anything quoted, compound, temporary or merely mentioned stores nothing.
"""
import re

from .storage import PREFERENCE_VALUES

ALL_KEYS = tuple(PREFERENCE_VALUES)

_LENGTH = {"concise": "concise", "short": "concise", "brief": "concise",
           "detailed": "detailed", "longer": "detailed", "thorough": "detailed"}
_ADJECTIVE = "|".join(_LENGTH)
_NOUN = r"(?:customer )?(?:replies|responses)"
_NO_INTRO = r"(?: without (?:unnecessary )?introductions)?"
_DURABLE = r"(?:from now on|going forward|in future|in the future|by default)"
# Lead-ins that state a standing preference by themselves, and ones that need a durable marker.
_STANDING = r"i prefer|i would prefer|always use|always keep"
_COMMAND = r"use|keep|i want|i would like"


def _normalize(message: str) -> str | None:
    text = re.sub(r"\bi['’]d\b", "i would", message.strip().lower()).rstrip(".! ")
    # Quotes, colons and multi-part messages are how reported speech and injected text arrive.
    if any(c in text for c in ('"', "'", "\n", ";", ":", "“", "”", "‘", "’")):
        return None
    return re.sub(r"\s+", " ", text)


def _clause(clause: str) -> dict[str, str]:
    """One exact supported style clause, or nothing."""
    length = (re.fullmatch(rf"({_ADJECTIVE}) {_NOUN}({_NO_INTRO})", clause)
              or re.fullmatch(rf"{_NOUN} ({_ADJECTIVE})({_NO_INTRO})", clause))
    if length:
        result = {"length": _LENGTH[length.group(1)]}
        if length.group(2):
            result["introductions"] = "omit"
        return result
    if clause in ("bullet points", "bullets", "bulleted lists"):
        return {"format": "bullets"}
    if clause in ("paragraphs", "short paragraphs"):
        return {"format": "paragraphs"}
    if clause in ("no introductions", "replies without introductions", "replies without unnecessary introductions"):
        return {"introductions": "omit"}
    if clause in ("introductions", "replies with introductions"):
        return {"introductions": "include"}
    return {}


def extract_preferences(message: str) -> dict[str, str]:
    text = _normalize(message)
    if text is None:
        return {}
    match = re.fullmatch(rf"(?:actually,? )?(?:(?P<before>{_DURABLE}),? )?(?:please )?(?P<lead>{_STANDING}|{_COMMAND}) (?P<body>.+)", text)
    if not match:
        return {}
    body = match.group("body")
    after = re.search(rf",? {_DURABLE}$", body)
    if after:
        body = body[:after.start()]
    if not (after or match.group("before") or re.fullmatch(_STANDING, match.group("lead"))):
        # A one-reply formatting instruction is not a durable preference.
        return {}
    parts = re.split(r",? (?:instead of|rather than|not) ", body, maxsplit=1)
    if len(parts) == 2 and not _clause(parts[1]):
        return {}
    # Exact supported clauses keep incidental prose out of memory, and a negated old
    # value never competes with the positive replacement clause.
    return _clause(parts[0])


_FORGET_TARGETS = (  # most specific first
    (r"(?:length|verbosity) preferences?", ("length",)),
    (r"(?:format|formatting|layout) preferences?", ("format",)),
    (r"introductions? preferences?", ("introductions",)),
    (r"(?:(?:customer|reply|writing|communication|style) )*preferences", ALL_KEYS),
    (r"everything(?: you (?:know|remember|have stored))?(?: about me)?", ALL_KEYS),
)


def parse_forget(message: str) -> tuple[str, ...] | None:
    """Preference keys the operator asked to delete, or None if this is not such a request.

    Deletion is destructive, so a negated or unrecognized sentence deletes nothing.
    """
    text = _normalize(message)
    if text is None or re.search(r"\b(?:do not|dont|never|not) forget\b", text):
        return None
    match = re.fullmatch(r"(?:please |can you |could you )?(?:forget|delete|remove|clear|erase) "
                         r"(?:all (?:of )?)?(?:my |the |our )?(?:stored |saved )?(?P<target>.+?)(?: please)?", text)
    if not match:
        return None
    for pattern, keys in _FORGET_TARGETS:
        if re.fullmatch(pattern, match.group("target")):
            return keys
    return None
