"""YIFY Subtitles, matched to the release names its sister site YTS mints.

Films only - probed live, a TV series IMDb id answers 404 on this site, which
tracks: YTS itself never releases a show, only films, and this is its
subtitle half.

Two things had to be found rather than assumed, because both are the reason
this was written off once already.

**The obvious domain looked like dead weight and was not, quite.**
`yifysubtitles.ch` answers the search page fine, and a plain client's first
download attempt comes back **403** with `Cf-Mitigated: challenge` - which
this add-on took, once, for the whole story. It is not one: measured
against Kodi POV IL's own Yify source, a real browser `User-Agent` and a
matching `Referer` reach the file behind it cleanly, **200**, no JavaScript
run anywhere - Cloudflare's rule here is checking the request's shape, not
proving a browser sits behind it. `http.py`'s own default is
`"Pinky/0.1 (Kodi)"`, which is exactly the shape that rule is for. The URL
was also wrong twice over in the attempt that produced the 403: `/subtitle/`
is singular, not `/subtitles/`, which is the path this add-on's own search
results give no reason to expect.

None of that ended up mattering, because `yts-subs.com` needed no spoofing
at all - measured identical counts for the same title on both, and this
add-on's own default headers are enough. It is used here for that reason,
not because the primary domain turned out to be unreachable; a future
provider willing to send a browser `User-Agent` and `Referer` could use
`yifysubtitles.ch` directly. Its own frontend downloads through a second
subdomain, `subtitles.yts-subs.com`, reached by a base64 `data-link`
attribute on the *detail* page. Decoding it once showed the whole thing was
unnecessary: the value is nothing but the row's own slug, already sitting in
the search page's href, moved to that subdomain with `.zip` appended -

    /subtitles/train-to-busan-2016-arabic-yify-35153
    -> https://subtitles.yts-subs.com/subtitles/train-to-busan-2016-arabic-yify-35153.zip

- so the transform is done here without ever fetching a detail page, which
matters on a site offering fifty-odd rows for one popular film: one request
finds them all, and only the row actually chosen costs a second one.

**The site's own language name was being measured wrong.** An earlier survey
of this exact catalogue (see CLAUDE.md) reported it near-empty, built on
`re.findall(r"flag-(\\w+)", page)` - which does not match a subtitle row at
all. It matches the empty `flag flag-""` class every row carries and a
handful of unrelated site furniture, and the "hits" it did report - `il`,
`gb`, `sa` - were an unrelated language-picker dropdown in the page chrome,
not subtitle rows. The real marker is a `sub-lang` span holding the language
by name - `english`, `Arabic` - inconsistently cased between pages, which is
why the count came out at zero even on a page carrying real subtitles: a
case-sensitive scan of a page whose casing was not what was assumed. Measured
correctly afterwards: The Handmaiden carries one Hebrew subtitle, and English
alone runs into the hundreds for a popular film - Train to Busan 211, Parasite
218 - which is exactly what `subs.ai.*`'s translation route wants, since a
release named to match a real YTS torrent scores 100 rather than whatever an
uploader's own name earns against it.
"""
import re

from ... import http
from . import common

NAME = "yify"
BASE = "https://yts-subs.com"
DOWNLOAD_HOST = "https://subtitles.yts-subs.com"

# The language a subtitle row names, as this site's own template writes it -
# inconsistently cased, hence the lowercasing before the lookup. Limited to
# what this add-on ever asks for rather than the whole of the site's own
# language list: a name with no entry here is a language nobody requested,
# and skipping it costs nothing.
_LANGUAGE_NAMES = {
    "hebrew": "he", "english": "en", "arabic": "ar", "polish": "pl",
    "spanish": "es", "russian": "ru", "french": "fr", "turkish": "tr",
    "italian": "it", "portuguese": "pt", "brazilian portuguese": "pt",
    "german": "de", "dutch": "nl", "greek": "el", "japanese": "ja",
    "korean": "ko", "chinese": "zh",
}

_ROW = re.compile(
    r'<tr\s[^>]*data-id="\d+"[^>]*>(.*?)</tr>', re.S)
_LANGUAGE = re.compile(r'sub-lang">\s*([^<]+?)\s*<', re.I)
_SLUG = re.compile(r'href="/subtitles/([a-z0-9-]+)"')
_RELEASE = re.compile(r'text-muted">subtitle</span>\s*([^<]+)')


def supports(language):
    return language in _LANGUAGE_NAMES.values()


def search(meta, target, languages):
    del target
    if meta.get("type") != "movie":
        return []
    imdb = (meta.get("ids") or {}).get("imdb")
    if not imdb:
        return []
    wanted = set(languages or [])
    if not wanted.intersection(_LANGUAGE_NAMES.values()):
        return []

    response = http.get("%s/movie-imdb/%s" % (BASE, imdb), timeout=(4, 8))
    if response is None or response.status_code != 200:
        return []
    try:
        html = response.text
    except Exception:
        return []

    found = []
    for row in _ROW.findall(html or ""):
        language_match = _LANGUAGE.search(row)
        slug_match = _SLUG.search(row)
        release_match = _RELEASE.search(row)
        if not (language_match and slug_match and release_match):
            continue
        code = _LANGUAGE_NAMES.get(language_match.group(1).strip().lower())
        if not code or code not in wanted:
            continue
        found.append(common.candidate(
            NAME, code, release_match.group(1).strip(),
            "%s/subtitles/%s.zip" % (DOWNLOAD_HOST, slug_match.group(1))))
    return found


def download(candidate):
    data = common.fetch_bytes(candidate["download"])
    return common.extract_subtitle(data, candidate.get("language", ""),
                                   candidate=candidate)
