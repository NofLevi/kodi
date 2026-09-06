"""Kids mode: a home screen a child can be left alone with.

The honest problem with a "safe mode" built out of filtering is that it is only
as good as the metadata, and metadata is patchy. A TMDB list result carries no
certification at all, so a filter that waits to see one either blocks
everything or lets everything through.

So kids mode does not filter the normal home screen. It replaces it. The rows
become ones that are safe by construction, because TMDB is asked for a
certification ceiling and family genres rather than asked for everything and
then trimmed. A row that cannot express that constraint - anything from Trakt,
the source picker, the whole Israeli live television list - is simply not
offered while kids mode is on.

Filtering still exists, as a second line rather than the first. Anything that
does reach an item with genres or a certification on it, which is everything
past the details screen, is checked before it plays. Between the two, a title
has to be safe by the row it came from and safe by its own metadata.

Leaving kids mode needs the PIN. It is stored as a salted hash rather than as
the digits, not because this resists a determined adult with the settings file
open, but because a PIN reused from a phone should not sit in plain text.
"""
import hashlib
import os

from . import kodi, settings

# Ordered from most to least permissive within each system. A title is allowed
# when its certification appears at or below the configured ceiling.
CERTIFICATIONS = [
    # films (US), then television (US), then the common European shapes
    "G", "TV-Y", "TV-Y7", "TV-G", "0", "U",
    "PG", "TV-PG", "6", "7",
    "PG-13", "TV-14", "12", "12A", "13", "14",
    "R", "TV-MA", "15", "16", "17", "18", "NC-17", "X",
]

# Where each ceiling sits in the list above.
CEILINGS = {
    "young": "TV-G",        # G / U / TV-G and below
    "older": "TV-PG",       # adds PG
    "teen": "TV-14",        # adds PG-13
}

# Genres that are never shown in kids mode, whatever the certification says.
# A documentary about war carries no rating in most of TMDB's data.
BLOCKED_GENRES = {
    "horror", "thriller", "crime", "war", "war & politics", "news",
}

_SALT = "katan-kids-v1"


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


def enabled():
    return settings.get_bool("kids.enabled", False)


def ceiling():
    """The configured certification ceiling, as a certification string."""
    return CEILINGS.get(settings.get("kids.age", "older"), "TV-PG")


def _hash(pin):
    return hashlib.sha256((_SALT + str(pin or "")).encode("utf-8")).hexdigest()


def has_pin():
    return bool(settings.get("kids.pin_hash", ""))


def set_pin(pin):
    """Store a PIN. An empty PIN removes it."""
    if not str(pin or "").strip():
        settings.set("kids.pin_hash", "")
        return False
    settings.set("kids.pin_hash", _hash(pin))
    return True


def check_pin(pin):
    """Is this the PIN? True when no PIN has been set at all."""
    stored = settings.get("kids.pin_hash", "")
    if not stored:
        return True
    return _hash(pin) == stored


def turn_on():
    settings.set("kids.enabled", "true")
    kodi.log("kids mode on")


def turn_off(pin=""):
    """Leave kids mode. Refuses without the PIN, which is the whole point."""
    if not check_pin(pin):
        kodi.log("kids mode: wrong PIN")
        return False
    settings.set("kids.enabled", "false")
    kodi.log("kids mode off")
    return True


# --------------------------------------------------------------------------
# what is allowed
# --------------------------------------------------------------------------


def _rank(certification):
    text = str(certification or "").strip().upper()
    if not text:
        return -1
    # "US:PG-13" and "PG-13 (Israel)" both appear in the wild.
    for separator in (":", "(", "/"):
        if separator in text:
            text = text.split(separator)[-1 if separator == ":" else 0].strip()
    try:
        return CERTIFICATIONS.index(text)
    except ValueError:
        return -1


def certification_allowed(certification):
    """Is this certification within the ceiling?

    An unknown or missing certification is allowed here, because rejecting it
    would empty every row: TMDB list results carry none. The genre check and
    the choice of rows are what actually carry kids mode.
    """
    rank = _rank(certification)
    if rank < 0:
        return True
    return rank <= _rank(ceiling())


def genres_allowed(genres):
    lowered = {str(name).strip().lower() for name in genres or []}
    return not (lowered & BLOCKED_GENRES)


def allows(item):
    """May a child see this item?"""
    if not enabled():
        return True
    if not isinstance(item, dict):
        return False
    if (item.get("extra") or {}).get("adult"):
        return False
    if not genres_allowed(item.get("genres")):
        return False
    return certification_allowed(item.get("mpaa"))


def filter_items(entries):
    """Drop anything a child should not see. A no-op when kids mode is off."""
    if not enabled():
        return list(entries or [])
    return [item for item in entries or [] if allows(item)]


# --------------------------------------------------------------------------
# which rows exist while kids mode is on
# --------------------------------------------------------------------------

# Rows that are safe by construction, in the order a child sees them.
ROW_IDS = ["kids_movies", "kids_shows", "kids_anime", "kids_israel"]


def rows_allowed(row_ids):
    """Restrict a row list to the kid-safe ones."""
    if not enabled():
        return list(row_ids or [])
    return [row_id for row_id in row_ids or [] if row_id in ROW_IDS]
