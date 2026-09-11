"""Shared plumbing for subtitle providers.

Providers differ in how you search them, but they all end up handing back
either an SRT or a zip containing one, so downloading and unpacking lives here.
"""
import hashlib
import io
import os
import re
import zipfile
import zlib

from ... import http, kodi

SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ass", ".ssa", ".vtt", ".txt")
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024      # a subtitle is never larger than this
MAX_ARCHIVE_ENTRIES = 200
MAX_ARCHIVE_NAME_BYTES = 64 * 1024
MAX_GZIP_MEMBERS = 8

LANGUAGE_ALIASES = {
    "he": {"he", "heb", "hebrew"},
    "en": {"en", "eng", "english"},
    "es": {"es", "spa", "spanish", "castellano"},
    "ar": {"ar", "ara", "arabic"},
    "pt": {"pt", "por", "portuguese"},
    "fr": {"fr", "fre", "fra", "french"},
    "ru": {"ru", "rus", "russian"},
    "de": {"de", "ger", "deu", "german", "deutsch"},
    "it": {"it", "ita", "italian", "italiano"},
    "tr": {"tr", "tur", "turkish"},
    "pl": {"pl", "pol", "polish"},
    "nl": {"nl", "dut", "nld", "dutch"},
    "ro": {"ro", "rum", "ron", "romanian"},
    "cs": {"cs", "cze", "ces", "czech"},
    "hu": {"hu", "hun", "hungarian"},
    "uk": {"uk", "ukr", "ukrainian"},
    "ja": {"ja", "jpn", "japanese"},
    "ko": {"ko", "kor", "korean"},
    "zh": {"zh", "chi", "zho", "chs", "cht", "chinese"},
    "hi": {"hi", "hin", "hindi"},
    "sr": {"sr", "srp", "serbian"},
    "bg": {"bg", "bul", "bulgarian"},
    "mk": {"mk", "mkd", "mac", "macedonian"},
    "el": {"el", "ell", "gre", "greek"},
    "fa": {"fa", "fas", "per", "persian", "farsi"},
    "ur": {"ur", "urd", "urdu"},
    "bn": {"bn", "ben", "bengali"},
}


def candidate(provider, language, release_name, download, **extra):
    """One search result, in the shape the matcher expects."""
    entry = {
        "provider": provider,
        "language": language,
        "release": release_name or "",
        "download": download,
        "score": 0,
        "reason": "",
    }
    entry.update(extra)
    return entry


def episode_numberings(meta):
    """Every (season, episode) an episode may be filed under, likeliest first.

    Empty for a film. One pair for almost every episode. Two for an anime
    whose absolute number differs from its season-relative one, because the
    two numbering schemes disagree about long-running anime and the
    subtitle indexes follow IMDb rather than TMDB: TMDB's Reborn season 8
    episode 14 is episode 149 counted from the first, and a site asked only
    for 8x14 answers with nothing - or with episode 14. `meta["absolute"]`
    is set only for anime (play._name_it_the_way_the_indexes_do), so this
    costs nothing for anything else.

    Asking both is safe. A row found under the second numbering still has to
    get past the matcher, which knows the absolute number too and scores a
    wrong episode at zero.
    """
    if meta.get("type") != "episode":
        return []
    numberings = [(int(meta.get("season") or 1), int(meta.get("episode") or 1))]
    absolute = int(meta.get("absolute") or 0)
    if absolute and (1, absolute) not in numberings:
        numberings.append((1, absolute))
    return numberings


def fetch_bytes(url, timeout=(5, 15), **kwargs):
    response = http.get(url, timeout=timeout, stream=True, **kwargs)
    if response is None or response.status_code >= 400:
        return b""
    try:
        # Read transport bytes only.  Letting urllib/requests decode here can
        # inflate a tiny Content-Encoding body before our size limit applies.
        data = response.raw.read(MAX_DOWNLOAD_BYTES + 1,
                                 decode_content=False)
    except Exception:
        return b""
    finally:
        try:
            response.close()
        except Exception:
            pass
    if len(data) > MAX_DOWNLOAD_BYTES:
        kodi.log("subtitle download was implausibly large, ignoring it")
        return b""
    encoding = (response.headers.get("Content-Encoding") or "").lower().strip()
    if encoding in ("gzip", "x-gzip"):
        return decompress_gzip(data)
    if encoding == "deflate":
        return decompress_deflate(data)
    return data


def decompress_gzip(data):
    """Inflate all gzip members with bounded output and transient memory."""
    if not data:
        return b""
    unpacked = bytearray()
    remaining_input = data
    members = 0
    try:
        while remaining_input:
            # RFC 1952 permits zero padding after the final member.
            if not remaining_input.strip(b"\0"):
                break
            members += 1
            if members > MAX_GZIP_MEMBERS:
                kodi.log("subtitle gzip contained too many members")
                return b""
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            cursor = 0
            while not decoder.eof:
                if decoder.unconsumed_tail:
                    chunk = decoder.unconsumed_tail
                elif cursor < len(remaining_input):
                    chunk = remaining_input[cursor:cursor + 64 * 1024]
                    cursor += len(chunk)
                else:
                    return b""
                room = MAX_DOWNLOAD_BYTES + 1 - len(unpacked)
                if room <= 0:
                    return b""
                unpacked.extend(decoder.decompress(chunk, min(64 * 1024,
                                                                room)))
                if len(unpacked) > MAX_DOWNLOAD_BYTES:
                    kodi.log("subtitle gzip expanded beyond subtitle limit")
                    return b""
            remaining_input = decoder.unused_data + remaining_input[cursor:]
    except zlib.error:
        return b""
    return bytes(unpacked)


def decompress_deflate(data):
    """Inflate an HTTP deflate body with the same output ceiling."""
    if not data:
        return b""
    for window_bits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            decoder = zlib.decompressobj(window_bits)
            unpacked = decoder.decompress(data, MAX_DOWNLOAD_BYTES + 1)
            remaining = MAX_DOWNLOAD_BYTES + 1 - len(unpacked)
            if remaining:
                unpacked += decoder.flush(remaining)
            if (not decoder.eof or decoder.unused_data
                    or decoder.unconsumed_tail):
                continue
            if len(unpacked) > MAX_DOWNLOAD_BYTES:
                kodi.log("subtitle deflate expanded beyond subtitle limit")
                return b""
            return unpacked
        except zlib.error:
            continue
    return b""


def extract_subtitle(data, prefer_language="", candidate=None):
    """Return subtitle bytes from a raw download, unzipping when needed."""
    if not data:
        return b""
    if data[:2] != b"PK":
        return data
    if candidate is not None:
        candidate["archive_fingerprint"] = hashlib.sha256(data).hexdigest()

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipfile:
        kodi.log("subtitle archive was not readable")
        return b""

    try:
        entries = archive.infolist()
        if (len(entries) > MAX_ARCHIVE_ENTRIES
                or sum(len(info.filename) for info in entries)
                > MAX_ARCHIVE_NAME_BYTES):
            kodi.log("subtitle archive metadata was implausibly large")
            return b""
        names = [info.filename for info in entries
                 if info.filename.lower().endswith(SUBTITLE_EXTENSIONS)
                 and not info.filename.startswith("__MACOSX")]
        if not names:
            return b""

        chosen = _pick_from_archive(names, prefer_language)
        if not chosen:
            kodi.log("subtitle archive had no unambiguous requested language")
            return b""
        info = archive.getinfo(chosen)
        if info.file_size > MAX_DOWNLOAD_BYTES:
            kodi.log("subtitle archive entry was implausibly large, ignoring it")
            return b""
        with archive.open(info) as handle:
            unpacked = handle.read(MAX_DOWNLOAD_BYTES + 1)
        return unpacked if len(unpacked) <= MAX_DOWNLOAD_BYTES else b""
    except (KeyError, RuntimeError, zipfile.BadZipfile):
        return b""
    finally:
        archive.close()


def _pick_from_archive(names, prefer_language):
    """Choose one unambiguous language/file, preferring SRT only afterward."""
    choices = list(names)

    def stem(name):
        return os.path.splitext(name.lower())[0]

    def preferred(entries):
        srt_entries = [name for name in entries if name.lower().endswith(".srt")]
        return sorted(srt_entries or entries)[0]

    if not prefer_language:
        return preferred(choices)

    code = prefer_language.lower()
    aliases = LANGUAGE_ALIASES.get(code, {code})
    short_aliases = {alias for alias in aliases if len(alias) == 2}
    long_aliases = aliases - short_aliases
    trailing_modifiers = {"forced", "foreign", "sdh", "cc", "hi", "default"}
    tagged = []
    for name in choices:
        all_tokens = [part for part in re.split(r"[^a-z0-9]+", name.lower())
                      if part]
        base_tokens = [part for part in re.split(
            r"[^a-z0-9]+", os.path.splitext(os.path.basename(name.lower()))[0])
                       if part]
        # Two-letter words collide constantly with titles (He, It, Ar). Treat
        # them as a language code only in the conventional suffix position,
        # allowing standard accessibility/forced modifiers after the code.
        while base_tokens and base_tokens[-1] in trailing_modifiers:
            base_tokens.pop()
        short_match = bool(base_tokens and base_tokens[-1] in short_aliases)
        if short_match or set(all_tokens) & long_aliases:
            tagged.append(name)

    if not tagged:
        return choices[0] if len(choices) == 1 else ""
    stems = {stem(name) for name in tagged}
    if len(stems) != 1:
        return ""
    return preferred(tagged)


def save(data, directory, name):
    """Write subtitle bytes to disk as UTF-8 SRT, returning the path."""
    from .. import srt

    if not data:
        return ""
    cues = srt.parse(srt.decode(data))
    if not cues:
        return ""
    if not os.path.isdir(directory):
        os.makedirs(directory)
    return srt.write(os.path.join(directory, name), srt.clean(cues))
