"""The normalised source record.

Every provider, however different its API, converts its results into this one
shape. Ranking, cache checking, the picker and playback all read it, so adding
a provider never means touching anything downstream.

    hash        40 character lowercase infohash, the identity of a torrent
    magnet      magnet URI when known
    url         direct stream URL when a provider already resolved one
    title       the release name, which is what the parser reads
    provider    which provider produced this
    size        bytes, 0 when unknown
    seeders     0 when unknown
    quality     resolution label from the release parser
    codec       h264 / h265 / av1 / xvid
    audio       audio format label
    hdr         list of hdr flags
    languages   language hints found in the release name
    group       release group, the strongest subtitle correlation signal
    cached      whether a debrid service already holds it
    cached_by   which service, when cached
    file_index  which file inside a pack, when the provider told us
    file_name   that file name, when known
    score       set by scoring.rank
"""
import re

from ..utils import release

_HASH = re.compile(r"\b([a-fA-F0-9]{40})\b")
_MAGNET_HASH = re.compile(r"btih:([a-zA-Z0-9]{32,40})", re.I)


def new_source(**fields):
    source = {
        "hash": "",
        "magnet": "",
        "url": "",
        "title": "",
        "provider": "",
        "size": 0,
        "seeders": 0,
        "quality": "sd",
        "codec": "unknown",
        "audio": "unknown",
        "dub": "",
        "hdr": [],
        "languages": [],
        "group": "",
        "cached": False,
        "cached_by": "",
        "file_index": None,
        "file_name": "",
        "score": 0.0,
        "extra": {},
    }
    source.update(fields)
    return source


def from_release_name(title, provider, size=0, seeders=0, info_hash="",
                      magnet="", url="", **extra):
    """Build a source by parsing its release name."""
    parsed = release.parse(title, size)
    return new_source(
        hash=normalise_hash(info_hash or magnet),
        magnet=magnet,
        url=url,
        title=title or "",
        provider=provider,
        size=int(size or 0),
        seeders=int(seeders or 0),
        quality=parsed["resolution"],
        codec=parsed["codec"],
        audio=parsed["audio"],
        dub=parsed["dub"],
        hdr=parsed["hdr"],
        languages=parsed["languages"],
        group=parsed["group"],
        extra=extra,
    )


def normalise_hash(value):
    """Pull a lowercase infohash out of a hash, magnet URI or arbitrary text."""
    if not value:
        return ""
    text = str(value)
    match = _MAGNET_HASH.search(text)
    if match:
        candidate = match.group(1)
        if len(candidate) == 40:
            return candidate.lower()
        return _from_base32(candidate)
    match = _HASH.search(text)
    if match:
        return match.group(1).lower()
    return ""


def _from_base32(value):
    """Older magnets carry a 32 character base32 hash."""
    import base64
    import binascii
    try:
        raw = base64.b32decode(value.upper())
        return binascii.hexlify(raw).decode("ascii").lower()
    except (binascii.Error, ValueError):
        return ""


def magnet_for(source, trackers=()):
    """A magnet URI for a source, built from its hash when needed."""
    if source.get("magnet"):
        return source["magnet"]
    if not source.get("hash"):
        return ""
    magnet = "magnet:?xt=urn:btih:%s" % source["hash"]
    if source.get("title"):
        try:
            from urllib.parse import quote
        except ImportError:      # pragma: no cover
            from urllib import quote
        magnet += "&dn=%s" % quote(source["title"])
    for tracker in trackers:
        magnet += "&tr=%s" % tracker
    return magnet


def dedupe(sources):
    """Collapse repeats, keeping the most informative copy.

    Two different things are collapsed here and it is worth keeping them
    apart.

    **The same torrent**, reported by several providers with different
    metadata. Merging beats discarding: one may know the size, another the
    seeders, and a third may already know it is cached. Matched by infohash,
    so it is exact.

    **The same file in different torrents**, which the infohash cannot see and
    which is what the viewer is actually looking at. The same release gets
    re-uploaded, and each upload is a different torrent of byte-identical
    content - so the picker showed `The.Matrix.1999.1080p.BrRip.x264.YIFY.mp4`
    twice, one above the other, at the same 1.86 GB. Measured on a real
    search, Silo S01E01 had **one file occupying positions 1 to 8**: the
    picker shows six rows, so the viewer was offered one option six times and
    told it was six.

    Matched on the release name and the size to the megabyte together, which
    is deliberately strict. The name alone would collapse a 1080p and a 720p
    encode that happen to be named alike; the size alone would collapse two
    unrelated films. Both agreeing means the same file, and the copies are
    merged rather than dropped so the survivor keeps every provider that had
    it and the best seeder count anyone reported.
    """
    by_hash = _collapse(sources, _hash_key)
    adopted = _adoptable_groups(by_hash)
    return _collapse(by_hash, lambda s: _file_key(s, adopted))


def _hash_key(source):
    """The same torrent: exact, and the only certain answer."""
    return source.get("hash") or ("t:" + (source.get("title") or "").lower())


def _shape(source):
    """Size to the megabyte, resolution and codec: what an encode weighs."""
    size = source.get("size") or 0
    if not size:
        return None
    return (int(round(size / float(1024 ** 2))),
            source.get("quality") or "", source.get("codec") or "")


def _adoptable_groups(sources):
    """Shapes where exactly one named group exists, so an unnamed one belongs.

    The case this is for: a re-upload whose name has been mangled past the
    point where the group can be read - "...H264-Ralf-PSOTNIK HT.mkv" beside
    "...H264-Ralf.mkv" at the same 4.80 GB. The unnamed one is the named one.

    Restricted to *exactly one* named group, and that restriction is the whole
    safety of it. Measured on a real search, six different releases of one
    Silo episode share 4977 MB - LostFilm, EniaHD, an Italian one, a Spanish
    one, BlackBit - and an unnamed source at that size could belong to any of
    them. Collapsing there would hide every non-English version behind one
    row, which is the opposite of showing a viewer their options.
    """
    seen = {}
    for source in sources:
        group = (source.get("group") or "").lower()
        shape = _shape(source)
        if not group or shape is None:
            continue
        seen.setdefault(shape, set()).add(group)
    return {shape: next(iter(groups))
            for shape, groups in seen.items() if len(groups) == 1}


def _file_key(source, adopted=None):
    """The same file, under two different names.

    Everything in one of these lists is already the answer to a search for
    one film or one episode, which is what makes this safe to do at all: the
    question is never "are these the same film" but only "are these the same
    encode of it".

    Two rules, and both need the size, rounded to the megabyte because one
    provider counts the torrent where another counts the file inside it.

    With a **release group**, the group and the size together are the answer,
    confirmed by the resolution and the codec. That is what catches a
    re-upload renamed on the way - "silo.s01e01.1080p.web.h264-ggwp.mkv" and
    "silo.s01e01.1080p.web.h264-ggwp[eztv.re].mkv" at the same 4.55 GB, or
    the same release with an "8" stuck on the front of the show name. Both
    were sitting one above the other in the picker.

    Without one, the **name** and the size. Strict, because there is nothing
    else to confirm it with.

    A source with no size at all is left alone. Collapsing on a name by
    itself is how a 720p encode gets eaten by a 1080p one that shares it.
    """
    shape = _shape(source)
    if shape is None:
        return _hash_key(source)

    group = (source.get("group") or "").lower()
    if not group:
        group = (adopted or {}).get(shape, "")
    if group:
        return "g:%s:%d:%s:%s" % ((group,) + shape)

    name = release.normalise(release.strip_subtitle_tags(
        release.strip_site_tags(
            (source.get("title") or "").rsplit("/", 1)[-1])))
    if not name:
        return _hash_key(source)
    return "f:%s:%d" % (name, shape[0])


def _collapse(sources, key_of):
    merged = {}
    order = []
    for source in sources:
        key = key_of(source)
        if not key or key == "t:":
            continue
        if key not in merged:
            merged[key] = dict(source)
            order.append(key)
            continue
        existing = merged[key]
        existing["size"] = max(existing.get("size") or 0, source.get("size") or 0)
        existing["seeders"] = max(existing.get("seeders") or 0,
                                  source.get("seeders") or 0)
        if source.get("cached") and not existing.get("cached"):
            existing["cached"] = True
            existing["cached_by"] = source.get("cached_by", "")
            # Cache ownership is a statement about one exact torrent/direct
            # handle. Never combine it with another equivalent release's hash.
            for field in ("hash", "magnet", "url", "file_id", "file_index",
                          "file_name"):
                if field in source:
                    existing[field] = source[field]
        if not existing.get("magnet") and source.get("magnet"):
            existing["magnet"] = source["magnet"]
        if not existing.get("url") and source.get("url"):
            existing["url"] = source["url"]
        if existing.get("file_index") is None and source.get("file_index") is not None:
            existing["file_index"] = source["file_index"]
            existing["file_name"] = source.get("file_name", "")
        # Keep whichever title parsed into more detail.
        if _detail(source) > _detail(existing):
            for field in ("title", "quality", "codec", "audio", "dub",
                          "hdr", "languages", "group"):
                existing[field] = source[field]
        existing.setdefault("providers", [])
        for name in (existing.get("provider"), source.get("provider")):
            if name and name not in existing["providers"]:
                existing["providers"].append(name)
    return [merged[key] for key in order]


def _detail(source):
    """How much a parsed title actually told us, used to pick the better one."""
    score = 0
    if source.get("quality") not in ("", "sd"):
        score += 2
    if source.get("codec") != "unknown":
        score += 1
    if source.get("audio") != "unknown":
        score += 1
    if source.get("group"):
        score += 1
    score += len(source.get("hdr") or [])
    return score


def label(source):
    """One line describing a source, for the picker and the log."""
    bits = [source.get("quality", "").upper() or "SD"]
    if source.get("hdr"):
        bits.append("/".join(flag.upper() for flag in source["hdr"]))
    if source.get("codec") not in ("unknown", ""):
        bits.append(source["codec"].upper())
    if source.get("size"):
        bits.append(release.size_label(source["size"]))
    if source.get("seeders"):
        bits.append("%dS" % source["seeders"])
    if source.get("group"):
        bits.append(source["group"].upper())
    # Anime only, in practice, and the one thing that decides between two rows
    # that are otherwise identical: whether this is the English dub or the
    # original audio. Nothing said it before, so choosing meant guessing.
    if source.get("dub"):
        bits.append(source["dub"].upper())
    return " | ".join(b for b in bits if b)
