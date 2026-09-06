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
    """Collapse repeats by infohash, keeping the most informative copy.

    Different providers return the same torrent with different metadata, so
    merging beats discarding: one may know the size, another the seeders, and
    a third may already know it is cached.
    """
    merged = {}
    order = []
    for source in sources:
        key = source.get("hash") or ("t:" + (source.get("title") or "").lower())
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
        if not existing.get("magnet") and source.get("magnet"):
            existing["magnet"] = source["magnet"]
        if not existing.get("url") and source.get("url"):
            existing["url"] = source["url"]
        if existing.get("file_index") is None and source.get("file_index") is not None:
            existing["file_index"] = source["file_index"]
            existing["file_name"] = source.get("file_name", "")
        # Keep whichever title parsed into more detail.
        if _detail(source) > _detail(existing):
            for field in ("title", "quality", "codec", "audio", "hdr",
                          "languages", "group"):
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
    return " | ".join(b for b in bits if b)
