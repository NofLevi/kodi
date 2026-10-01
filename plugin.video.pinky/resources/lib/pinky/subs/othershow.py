"""A subtitle filed under our show that names another one.

Wizdom files an upload under every show whose title *contains* the name it
was uploaded with. Asked for Gilmore Girls it also answers with HBO's Girls -
69 of its 191 subtitles - and for CSI: Miami with the original CSI, 65 of 159;
The Middle sits under Malcolm in the Middle, Chicago Med under Chicago Fire,
Stargate Atlantis under SG-1. They carry the right season and episode, so the
matcher gave them the title and the episode: for Gilmore Girls 6x05 the best
Hebrew subtitle on offer was "Girls.S06E05.720p.HDTV.x264-AVS" at 99%, ahead
of the real one at 85, and for CSI: Miami 9x13 the only one was CSI's.

Words alone cannot settle it. "Buffy" under Buffy the Vampire Slayer is ours,
and so are "Lois and Clark", "SG1" and "Hawaii Five"; "Girls" under Gilmore
Girls is not. What separates them is whether a *different* show goes by the
name, and TMDB knows: one cached search per name that is not plainly ours.

A franchise shares its short name - "CSI" is CSI: Crime Scene Investigation
and is also what CSI: Miami goes by - and there the plain name is the first
show's, which is how uploaders use it.
"""
import re

from .. import http, kodi
from ..utils import release

# What an uploader adds to a name without naming another show.
_FURNITURE = frozenset("heb hebrew hebsub hebsubs sub subs eng english us uk".split())
# A wrong show brings one unfamiliar name or two; a title like "It" is
# answered with a dozen. The commonest are asked about, together, and a
# search that is slow costs the question rather than the subtitles.
MAX_ASKED = 8
DEADLINE = 4.0


def drop(candidates, meta):
    """The candidates without those that name another show."""
    meta = meta or {}
    if meta.get("type") != "episode" or (meta.get("extra") or {}).get("anime"):
        return candidates
    ours = [name for name in (_fold(name) for name in _our_names(meta)) if name]
    if not ours:
        return candidates
    from ..sources import scoring
    heads = {}
    unfamiliar = {}
    for candidate in candidates:
        name = candidate.get("release") or candidate.get("name") or ""
        # A hash is the file itself, whatever it was named.
        if not name or candidate.get("hash_match"):
            continue
        stated = scoring.stated_series(name)
        head = heads[id(candidate)] = _fold(stated)
        if head in unfamiliar:
            unfamiliar[head][0] += 1
        elif not _plainly_ours(head, ours):
            unfamiliar[head] = [1, _as_typed(name, stated)]
    if not unfamiliar:
        return candidates
    asked = sorted(unfamiliar, key=lambda head: -unfamiliar[head][0])[:MAX_ASKED]
    from ..meta import tmdb
    answers = http.run_parallel(
        [(head, lambda query=unfamiliar[head][1]: tmdb.shows_named(query))
         for head in asked], workers=4, deadline=DEADLINE)
    another = {}
    for head in asked:
        owner = _owner(head, answers.get(head) or [], meta)
        if owner:
            another[head] = owner
    if not another:
        return candidates
    kept = [candidate for candidate in candidates
            if heads.get(id(candidate)) not in another]
    kodi.log("subtitles: %d of %d name another show (%s)"
             % (len(candidates) - len(kept), len(candidates),
                ", ".join(sorted(set(another.values())))))
    return kept


def _plainly_ours(head, ours):
    """Our name, or our name with only an uploader's furniture around it.

    Also what is too short to be asked about: a bare "s" or "it".
    """
    from ..sources import scoring
    words = set(head.split())
    if len(head) < 3 or not any(len(word) >= 3 for word in words):
        return True
    for name in ours:
        if name == head or name.replace(" ", "") == head.replace(" ", ""):
            return True
        if set(name.split()) <= words and all(
                word in _FURNITURE or word in scoring._WESTERN_STRUCTURAL
                or word.isdigit() for word in words - set(name.split())):
            return True
    return False


def _owner(head, rows, meta):
    """The other show that goes by this name, or ""."""
    mine = str((meta.get("ids") or {}).get("tmdb") or "")
    my_year = next((year for found, _, _, year in rows if found == mine and year),
                   str(meta.get("year") or "")) or "9999"
    shared = any(_short(name) == head for name in _our_names(meta))
    for found, name, original, year in rows:
        if found == mine:
            continue
        for other in (name, original):
            if not other or not _latin(other):
                continue
            if _fold(other) == head:
                return name
            # A franchise's short name is its first show's.
            if _short(other) == head and (not shared or (year and year < my_year)):
                return name
    return ""


def _our_names(meta):
    names = [meta.get(key) for key in
             ("title", "show_title", "original_title", "english_title")]
    names += list(meta.get("translated_titles") or [])
    # Latin only: "CSI:マイアミ" folds to "csi", and then CSI is one of CSI:
    # Miami's own names.
    return [name for name in names if name and _latin(name)]


def _latin(text):
    return all(ord(char) < 0x250 for char in text)


def _fold(text):
    """A name as plain words: no punctuation, no article, "&" spelled out."""
    text = re.sub(u"['`’ʼ]", "", text or "").replace("&", " and ")
    text = " ".join(re.sub(r"[^a-z0-9 ]+", " ", release.normalise(text)).split())
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            return text[len(article):]
    return text


def _short(name):
    """What a show with a subtitle of its own goes by: "CSI" for CSI: Miami."""
    return _fold(re.split(r":| - ", name or "", 1)[0])


def _as_typed(name, stated):
    """The stated name as the uploader spelled it, to search with.

    `normalise` folds a doubled vowel, which is right for comparing and wrong
    for asking: "The Good Doctor" is not found as "the god doctor".
    """
    typed = re.sub(r"[._]+", " ", re.sub(r"^\s*\[[^\]]*\]\s*", "", name)).split()
    return " ".join(typed[:len(stated.split())]) or stated
