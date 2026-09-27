# -*- coding: utf-8 -*-
"""Subtitles that ship inside the torrent, beside the video file.

The best subtitle for a release is the one that came with it, and it was
sitting there the whole time. Measured on Hikaru no Go 2x03, the source the
picker had been offering all week:

    Hikaru.No.Go.TV.EP33.BluRay.1080p.AC3.x264-CHD.mkv     the video
    Hikaru.No.Go.TV.EP33.BluRay.1080p.AC3.x264-CHD.srt     25 KB, 356 cues

One hundred and fifty-two files in that torrent, seventy-six of them
subtitles, one per episode, named to match. Nothing in this add-on had ever
looked at them.

Why it matters more than one more provider. A file that ships with the
release is in time **by construction** - it was typed against that exact cut
by whoever made it - so it needs no hash, no cross-language proof and no
correlation to trust. That is the whole of the sync problem for anime, where
a hash almost never exists and the cross-language proof is usually one
language short, solved by not needing a ruler at all.

It also makes an ideal translation source for the same reason: a translation
inherits its source's timing, and this source's timing is right.

The matcher scores it without being told anything. The subtitle's name *is*
the release name, so `_same_name` returns "identical release name" and 100,
which is exactly what it deserves.

Cost: one debrid lookup and a download of a few tens of kilobytes, and only
for the release actually being played.

Language is read from the filename where the release states it - `.he.srt`,
`.eng.srt`, `Hebrew.srt` - and assumed English otherwise, because an
unlabelled subtitle beside a fansub release is English far more often than
anything else. The guess is safe: `download_candidate` refuses cues whose
script is not the language asked for, so a wrong guess is discarded rather
than shown.
"""
import os
import re

from ... import kodi
from . import common

NAME = "sidecar"

SUBTITLE_EXTENSIONS = (".srt", ".ass", ".ssa", ".vtt", ".sub")

# How many subtitle files to offer from one torrent. A season pack holds one
# per episode - seventy-six on the measured one - and all but a handful are
# for other episodes. The matcher would score those at zero, but building
# seventy-six candidates to throw away is work a projector does not need.
MAX_FILES = 6

# `.he.srt`, `.heb.srt`, `.en.srt`, `-Hebrew.srt`: the language marker sits
# between the release name and the extension when there is one at all.
_LANGUAGE_TAG = re.compile(
    r"[._\-\[( ]("
    r"he|heb|hebrew|iw|en|eng|english|ar|ara|arabic|pl|pol|polish|"
    r"es|spa|spanish|fr|fre|fra|french|ru|rus|russian|pt|por|portuguese|"
    r"it|ita|italian|ja|jpn|japanese|zh|chi|chinese|ko|kor|korean"
    r")[._\-\])  ]*$", re.IGNORECASE)

_TO_CODE = {
    "he": "he", "heb": "he", "hebrew": "he", "iw": "he",
    "en": "en", "eng": "en", "english": "en",
    "ar": "ar", "ara": "ar", "arabic": "ar",
    "pl": "pl", "pol": "pl", "polish": "pl",
    "es": "es", "spa": "es", "spanish": "es",
    "fr": "fr", "fre": "fr", "fra": "fr", "french": "fr",
    "ru": "ru", "rus": "ru", "russian": "ru",
    "pt": "pt", "por": "pt", "portuguese": "pt",
    "it": "it", "ita": "it", "italian": "it",
    "ja": "ja", "jpn": "ja", "japanese": "ja",
    "zh": "zh", "chi": "zh", "chinese": "zh",
    "ko": "ko", "kor": "ko", "korean": "ko",
}

# What an unlabelled subtitle beside a release is, when it does not say.
DEFAULT_LANGUAGE = "en"


def supports(language):
    """Any language: what is in the torrent is what is in the torrent."""
    return bool(language)


def language_of(name, default=DEFAULT_LANGUAGE):
    """The language a subtitle filename declares, or the default."""
    stem = os.path.splitext(os.path.basename(str(name or "")))[0]
    match = _LANGUAGE_TAG.search(stem)
    if match:
        return _TO_CODE.get(match.group(1).lower(), default)
    return default


def _stem(name):
    return os.path.splitext(os.path.basename(str(name or "")))[0].lower()


def rank_files(files, video_name):
    """Subtitle files from one torrent, the ones for this episode first.

    A season pack holds one per episode and only one of them is ours. The
    name is what says which: a release ships `<video>.srt` beside
    `<video>.mkv`, so a subtitle whose stem starts with the video's stem is
    this episode's and everything else is somebody else's.
    """
    wanted = _stem(video_name)
    exact, prefixed, rest = [], [], []
    for entry in files or []:
        name = str(entry.get("name") or entry.get("short_name") or "")
        if not name.lower().endswith(SUBTITLE_EXTENSIONS):
            continue
        stem = _stem(name)
        if wanted and stem == wanted:
            exact.append(entry)
        elif wanted and stem.startswith(wanted):
            prefixed.append(entry)
        else:
            rest.append(entry)
    # Only fall back to the whole list when nothing matched by name; on a
    # season pack `rest` is seventy-five other episodes.
    ordered = exact + prefixed
    return (ordered or rest)[:MAX_FILES]


def search(meta, target, languages):
    """Candidates for the subtitle files inside the release being played."""
    source = (meta or {}).get("source") or {}
    if not source.get("torrent_hash"):
        return []

    from ...debrid import registry
    client = registry.client_with_sidecars(source)
    if client is None:
        return []

    try:
        files, link_for = client.sidecar_subtitles(source)
    except Exception:
        kodi.log_exception("could not list the subtitles inside the torrent")
        return []
    if not files:
        return []

    found = []
    for entry in rank_files(files, source.get("file_name")):
        name = str(entry.get("name") or entry.get("short_name") or "")
        url = link_for(entry)
        if not url:
            continue
        found.append(common.candidate(
            NAME, language_of(name),
            # The release name, deliberately: it is the video's name with a
            # different extension, so the matcher recognises it as the same
            # release and scores it 100 without being told anything.
            os.path.splitext(os.path.basename(name))[0], url,
            uploader=NAME, sidecar=True))
    if found:
        kodi.log("%d subtitle file(s) ship inside this release" % len(found))
    return found


def download(candidate):
    """The file itself, straight off the debrid link.

    No archive handling: what is inside a torrent beside the video is the
    subtitle, not a zip of one. `extract_subtitle` is still asked, because a
    release that ships a `.zip` of its subtitles is not unheard of and it
    costs nothing to be right about it.
    """
    data = common.fetch_bytes(candidate["download"])
    return common.extract_subtitle(data, candidate.get("language"),
                                   candidate=candidate)
