# -*- coding: utf-8 -*-
"""English subtitles for anime, as the source of a Hebrew translation.

Katan's subtitle providers are keyed by IMDb and serve films and live action
well; for anime they run out - Hikaru no Go has English for episodes 1-30 and
nothing after. Two archives fill that:

* AnimeTosho extracts the subtitle tracks from anime releases. Found by the
  exact file name Stremio sends, a track is timed to this very file.
* Kitsunekko keeps English subtitles for about 2,500 anime, often named for
  the release they were made for.
"""
import io
import lzma
import os
import re
import zipfile

try:
    from urllib.parse import quote, unquote_plus
except ImportError:                                   # pragma: no cover
    from urllib import quote, unquote_plus

ANIMETOSHO = "https://feed.animetosho.org/json"
ATTACHMENT = "https://storage.animetosho.org/attach/%08x/file.xz"
KITSUNEKKO = "https://kitsunekko.net"
SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt")
LOOKUP_TTL = 24 * 3600
LISTING_TTL = 7 * 24 * 3600
MAX_RESULTS = 6
MAX_BYTES = 8 * 1024 * 1024


# --------------------------------------------------------------------------
# AnimeTosho
# --------------------------------------------------------------------------


def from_animetosho(filename):
    """(cues, label) of English dialogue made for this exact file."""
    if not filename:
        return [], ""
    for query in _queries(filename):
        for hit in (_get_json({"q": query}) or [])[:MAX_RESULTS]:
            detail = _get_json({"show": "torrent", "id": hit.get("id")}) or {}
            for entry in detail.get("files") or []:
                if not same_file(entry.get("filename"), filename):
                    continue
                track = pick_track(entry.get("attachments"))
                if not track:
                    continue
                cues = _track_cues(track["id"])
                if cues:
                    name = (track.get("info") or {}).get("name") or "English"
                    return cues, "AnimeTosho: %s" % name
    return [], ""


def _queries(filename):
    """The release name to search by: whole, then just group, title and number."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = re.sub(r"\s*\[[0-9A-Fa-f]{8}\]\s*", " ", stem).strip()
    queries = [stem]
    short = re.match(r"^(\[[^\]]+\]\s*[^\[\(]+)", stem)
    if short and short.group(1).strip() != stem:
        queries.append(short.group(1).strip())
    return queries


def same_file(candidate, filename):
    """The same file, wherever in the torrent it sits."""
    def base(name):
        return os.path.basename((name or "").replace("\\", "/")).strip().lower()
    return bool(candidate) and base(candidate) == base(filename)


def pick_track(attachments):
    """The English dialogue track - never one that is only signs and songs."""
    best, best_score = None, None
    for attachment in attachments or []:
        if attachment.get("type") != "subtitle":
            continue
        info = attachment.get("info") or {}
        if (info.get("lang") or "eng").lower() not in ("eng", "en", "enm"):
            continue
        name = (info.get("name") or "").lower()
        if ("sign" in name or "song" in name) and "dialog" not in name:
            continue
        if info.get("forced"):
            continue
        score = (10 if ("dialog" in name or "full" in name) else 0) + \
            min(9, int(attachment.get("size") or 0) // 10000)
        if best_score is None or score > best_score:
            best, best_score = attachment, score
    return best


def _track_cues(attachment_id):
    from katan import http
    from katan.subs import srt

    try:
        response = http.get(ATTACHMENT % int(attachment_id), timeout=(5, 30))
        if response is None or response.status_code != 200:
            return []
        data = response.content
        if data[:6] == b"\xfd7zXZ\x00":
            data = lzma.decompress(data)
        return srt.clean(srt.parse(srt.decode(data, "en")))
    except Exception:
        return []


def _get_json(params):
    from katan import cache, http

    key = cache.make_key("animetosho", sorted(params.items()))
    return cache.cached(key, lambda: http.get_json(
        ANIMETOSHO, params=params, timeout=(5, 20), default=None), LOOKUP_TTL)


# --------------------------------------------------------------------------
# Kitsunekko
# --------------------------------------------------------------------------


def from_kitsunekko(titles, season, episode, absolute, filename):
    """(cues, label) for this episode from Kitsunekko, or ([], "")."""
    from katan.utils import release

    folder = find_folder(titles)
    if not folder:
        return [], ""
    best = None
    for path in _folder_files(folder):
        for name, load in _members(path):
            parsed = release.parse(name)
            if not (parsed.get("episode") or parsed.get("absolute")):
                continue
            if not release.matches_episode(parsed, season, episode, absolute):
                continue
            score = likeness(name, filename)
            if best is None or score > best[0]:
                best = (score, name, load)
    if best is None:
        return [], ""
    cues = best[2]()
    return (cues, "Kitsunekko: %s" % best[1]) if cues else ([], "")


def find_folder(titles):
    folders = _folders()
    wanted = [_simple(t) for t in titles if t]
    for title in wanted:
        if title in folders:
            return folders[title]
    for title in wanted:
        for simple, folder in folders.items():
            if len(title) >= 4 and simple.startswith(title):
                return folder
    return ""


def parse_folders(html):
    """{simplified name: folder} from Kitsunekko's English index."""
    found = {}
    for encoded in re.findall(
            r'href="/dirlist\.php\?dir=subtitles%2F([^"%]+(?:%[0-9A-Fa-f]{2}[^"%]*)*)%2F"',
            html):
        folder = unquote_plus(encoded)
        if "/" not in folder:
            found.setdefault(_simple(folder), folder)
    return found


def parse_files(html):
    return [unquote_plus(p) for p in re.findall(
        r'href="(subtitles/[^"]+\.(?:srt|ass|ssa|vtt|zip))"', html, re.I)]


def likeness(name, filename):
    """How much a subtitle's file name says it was made for this file."""
    from katan.utils import release

    score = 0.0
    group = release.release_group(filename or "")
    if group and release.release_group(name) == group:
        score += 50
    mine = set(release.normalise(name).split())
    theirs = set(release.normalise(filename or "").split())
    if mine and theirs:
        score += 50.0 * len(mine & theirs) / len(mine | theirs)
    return score


def _folders():
    from katan import cache, http

    def fetch():
        response = http.get(KITSUNEKKO + "/dirlist.php",
                            params={"dir": "subtitles/"}, timeout=(5, 30))
        if response is None or response.status_code != 200:
            return None
        return parse_folders(response.content.decode("utf-8", "replace")) or None

    return cache.cached(cache.make_key("kitsunekko", "folders"), fetch,
                        LISTING_TTL) or {}


def _folder_files(folder):
    from katan import cache, http

    def fetch():
        response = http.get(KITSUNEKKO + "/dirlist.php",
                            params={"dir": "subtitles/%s/" % folder},
                            timeout=(5, 30))
        if response is None or response.status_code != 200:
            return None
        return parse_files(response.content.decode("utf-8", "replace")) or None

    return cache.cached(cache.make_key("kitsunekko", "files", folder), fetch,
                        LOOKUP_TTL) or []


def _members(path):
    """(name, loader) for each subtitle a Kitsunekko file holds."""
    if not path.lower().endswith(".zip"):
        yield os.path.basename(path), lambda: _cues(_download(path))
        return
    data = _download(path)
    if not data:
        return
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipfile:
        return
    for info in archive.infolist():
        if info.filename.lower().endswith(SUBTITLE_EXTENSIONS):
            yield (os.path.basename(info.filename),
                   lambda info=info: _cues(archive.read(info)))


_downloads = {}


def _download(path):
    """File bytes, kept for the process: one zip holds a whole series."""
    from katan import http

    if path in _downloads:
        return _downloads[path]
    response = http.get(KITSUNEKKO + "/" + quote(path), timeout=(5, 60))
    data = b""
    if response is not None and response.status_code == 200:
        data = response.content[:MAX_BYTES]
    if len(_downloads) > 20:
        _downloads.clear()
    _downloads[path] = data
    return data


def _cues(data):
    from katan.subs import srt

    if not data:
        return []
    try:
        return srt.clean(srt.parse(srt.decode(data, "en")))
    except Exception:
        return []


def _simple(text):
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").lower()).split())
