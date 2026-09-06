"""Extra context that makes a Hebrew translation noticeably better.

Hebrew marks the speaker's gender on verbs and adjectives, so "I am tired" has
two correct translations depending on who says it. English does not carry that
information, which is why machine translation of English to Hebrew so often
gets it wrong in a way that reads as obviously foreign.

The Kodi POV IL build solves this by downloading an Arabic subtitle as a gender
oracle, because Arabic marks gender too, then aligning it line by line. That
works, and it costs an extra download, an alignment pass and a large prompt.

This takes a cheaper route to most of the same benefit: the cast list, which
TMDB already returns with a gender per person and which the add-on already
fetches for the details screen. Telling the model who is in the film and which
of them are women costs a few hundred bytes and no extra request.
"""
from ... import cache, kodi

MAX_PEOPLE = 12
TTL = 30 * 24 * 3600

GENDER_WORDS = {1: "female", 2: "male"}


def cast_note(meta):
    """A short line naming the cast and their gender, or an empty string."""
    people = _cast_for(meta)
    if not people:
        return ""

    described = []
    for person in people:
        word = GENDER_WORDS.get(person.get("gender") or 0)
        if not word:
            continue
        role = (person.get("role") or "").split("/")[0].strip()
        name = person.get("name") or ""
        if role and role.lower() not in ("self", "narrator"):
            described.append("%s (%s, played by %s)" % (role, word, name))
        elif name:
            described.append("%s (%s)" % (name, word))

    if not described:
        return ""
    return "; ".join(described[:MAX_PEOPLE])


def _cast_for(meta):
    """The cast, from the item in hand or from TMDB, cached per title."""
    item = meta.get("item") or {}
    if item.get("cast"):
        return item["cast"][:MAX_PEOPLE]

    ids = meta.get("ids") or {}
    tmdb_id = ids.get("tmdb")
    if not tmdb_id:
        return []

    key = cache.make_key("cast", meta.get("type"), tmdb_id)
    hit = cache.get(key)
    if hit is not None:
        return hit

    try:
        from ...meta import tmdb
        if not tmdb.has_key():
            return []
        detail = (tmdb.show(tmdb_id) if meta.get("type") in ("show", "episode")
                  else tmdb.movie(tmdb_id))
    except Exception:
        kodi.log_exception("could not fetch the cast for translation context")
        return []

    people = (detail or {}).get("cast") or []
    trimmed = [{"name": p.get("name", ""), "role": p.get("role", ""),
                "gender": p.get("gender", 0)} for p in people[:MAX_PEOPLE]]
    if trimmed:
        cache.set(key, trimmed, TTL)
    return trimmed


# --------------------------------------------------------------------------
# choosing what to translate from
# --------------------------------------------------------------------------

# Languages whose verbs and adjectives already carry the speaker's gender.
# Translating from one of these into Hebrew preserves it for free, so a
# slightly worse-matching Spanish subtitle can beat a better-matching English
# one. This is the same insight behind the Arabic oracle, without the download.
GENDER_MARKING = (
    "ar", "he", "es", "pt", "it", "fr", "ro", "ca",       # Semitic and Romance
    "ru", "pl", "cs", "uk", "sr", "hr", "bg", "sk",        # Slavic
    "hi", "ur", "pa",                                      # Indo-Aryan
)

# How much a gender-marking source is worth, in the same units the subtitle
# matcher scores in. Enough to break a near tie, not enough to pick a subtitle
# for the wrong episode.
GENDER_BONUS = 18


def source_bonus(language):
    """Extra score for translating out of a language that marks gender."""
    return GENDER_BONUS if (language or "").lower() in GENDER_MARKING else 0


def rank_translation_sources(winners, languages):
    """Order candidate source languages for translation, best first.

    Match quality still leads, because a well-matched English subtitle beats a
    badly matched Spanish one. The bonus only decides close calls.
    """
    candidates = []
    for language, candidate in (winners or {}).items():
        if language == (languages[0] if languages else ""):
            continue                       # that is the target language
        if not candidate:
            continue
        score = (candidate.get("score") or 0) + source_bonus(language)
        candidates.append((score, language, candidate))
    candidates.sort(key=lambda row: -row[0])
    return [(language, candidate) for _score, language, candidate in candidates]
