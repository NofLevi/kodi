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
from .. import matcher

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

# How much a source language is worth, in the same units the subtitle matcher
# scores in. The order is Arabic, then any other language that marks gender,
# then English, then Japanese, Korean and Chinese. Arabic first because it
# marks gender the way Hebrew does and is the closest to it, which is why POV's
# MoranSubs downloads an Arabic subtitle as a gender oracle. English carries no
# gender at all. The three East Asian languages are the last resort: often the
# original script of the show, but they drop the subject so often that the
# model is left guessing who is speaking as well as their gender.
#
# Bonuses rather than a strict order, and that is deliberate. A translation
# keeps its source's timings exactly, so an Arabic subtitle for a different cut
# becomes a beautifully gendered Hebrew subtitle that is two minutes out. The
# bonus decides between files that fit about as well; it cannot choose one
# that does not fit over one that does.
ARABIC_BONUS = 25
GENDER_BONUS = 18
SUBJECT_DROPPING = ("ja", "ko", "zh")
SUBJECT_DROPPING_PENALTY = 10


def source_bonus(language):
    """Extra score for the language a Hebrew translation starts from."""
    code = (language or "").lower()
    if code == "ar":
        return ARABIC_BONUS
    if code in GENDER_MARKING:
        return GENDER_BONUS
    if code in SUBJECT_DROPPING:
        return -SUBJECT_DROPPING_PENALTY
    return 0


# How far behind the best-fitting file a candidate may be and still have its
# language count for anything. The bonus is documented as winning a close
# call without overriding a clearly better match, and at 25 it was doing the
# opposite: measured on Attack on Titan 1x12, an Arabic file fitting 87 was
# chosen over an English one fitting **99**, and on Jujutsu Kaisen an Arabic
# 91 over a Russian 96. Twenty-five is larger than a whole rung of the
# matcher's ladder - 70 to 85 is fifteen - so it could jump one.
#
# A translation inherits its source's timing exactly. Well-gendered Hebrew
# that is out by a rung is worse than plainly-gendered Hebrew that fits.
CLOSE_CALL = 10


def eligible_for_bonus(score, best_score):
    """May this candidate's language count, given the best fit available?"""
    return int(score or 0) >= int(best_score or 0) - CLOSE_CALL


def rank_translation_candidates(candidates, target):
    """Rank individual files so one dead link cannot hide its same-language peer."""
    ranked = []
    seen = set()
    for position, candidate in enumerate(candidates or []):
        language = candidate.get("language", "")
        if not language or language == target:
            continue
        if candidate.get("evidence"):
            # A clock, not a subtitle. The language-less timing query returns
            # whatever the episode has - Vietnamese, Indonesian, Persian -
            # which is exactly what makes it a good ruler and exactly what
            # nobody wants translated into Hebrew on their behalf.
            continue
        identity = matcher.candidate_key(candidate, language)
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        ranked.append([_exact(candidate), candidate.get("score") or 0,
                       language, -position, candidate])
    # The bonus is applied afterwards, because it needs to know what the best
    # fit on offer is before it can tell a close call from a clearly better
    # match.
    best_score = max([row[1] for row in ranked] or [0])
    for row in ranked:
        bonus = (source_bonus(row[2])
                 if target == "he" and eligible_for_bonus(row[1], best_score)
                 else 0)
        row[1] = row[1] + bonus
    ranked.sort(key=lambda row: (-row[0], -row[1], -row[3]))
    return [(language, candidate)
            for _exact_file, _score, language, _position, candidate in ranked]


def _exact(candidate):
    """1 for a subtitle proven to belong to this exact file, else 0.

    It comes before the language bonus, not after it. A translation keeps its
    source's timings exactly, so a hash-matched English subtitle becomes Hebrew
    that fits by construction - and it was losing to a name-matched Arabic
    one, 100 + 0 against 76 + 25, whose timing is a guess. The bonus exists to
    choose between guesses, not to overrule a certainty.
    """
    return 1 if candidate.get("reason") == "hash" else 0


def rank_translation_sources(winners, languages):
    """Order candidate source languages for translation, best first.

    Match quality still leads, because a well-matched English subtitle beats a
    badly matched Spanish one. The bonus only decides close calls.
    """
    candidates = []
    target = languages[0] if languages else ""
    for language, candidate in (winners or {}).items():
        if language == target:
            continue                       # that is the target language
        if not candidate:
            continue
        bonus = source_bonus(language) if target == "he" else 0
        score = (candidate.get("score") or 0) + bonus
        candidates.append((_exact(candidate), score, language, candidate))
    candidates.sort(key=lambda row: (-row[0], -row[1]))
    return [(language, candidate)
            for _exact_file, _score, language, candidate in candidates]
