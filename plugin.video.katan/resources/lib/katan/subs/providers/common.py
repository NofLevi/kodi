"""Shared plumbing for subtitle providers.

Providers differ in how you search them, but they all end up handing back
either an SRT or a zip containing one, so downloading and unpacking lives here.
"""
import io
import os
import zipfile

from ... import http, kodi

SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ass", ".ssa", ".vtt", ".txt")
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024      # a subtitle is never larger than this


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


def fetch_bytes(url, timeout=(5, 15), **kwargs):
    response = http.get(url, timeout=timeout, stream=True, **kwargs)
    if response is None or response.status_code >= 400:
        return b""
    try:
        data = response.raw.read(MAX_DOWNLOAD_BYTES + 1, decode_content=True)
    except Exception:
        data = response.content[:MAX_DOWNLOAD_BYTES + 1]
    finally:
        try:
            response.close()
        except Exception:
            pass
    if len(data) > MAX_DOWNLOAD_BYTES:
        kodi.log("subtitle download was implausibly large, ignoring it")
        return b""
    return data


def extract_subtitle(data, prefer_language=""):
    """Return subtitle bytes from a raw download, unzipping when needed."""
    if not data:
        return b""
    if data[:2] != b"PK":
        return data

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipfile:
        kodi.log("subtitle archive was not readable")
        return b""

    names = [n for n in archive.namelist()
             if n.lower().endswith(SUBTITLE_EXTENSIONS)
             and not n.startswith("__MACOSX")]
    if not names:
        return b""

    chosen = _pick_from_archive(names, prefer_language)
    try:
        return archive.read(chosen)
    except KeyError:
        return b""


def _pick_from_archive(names, prefer_language):
    """Archives often hold several languages or several parts."""
    srt = [n for n in names if n.lower().endswith(".srt")] or names
    if prefer_language:
        tagged = [n for n in srt if prefer_language.lower() in n.lower()]
        if tagged:
            srt = tagged
    # The largest file is the full dialogue, not a forced-subtitle track.
    return max(srt, key=len) if len(srt) == 1 else sorted(srt)[0]


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
