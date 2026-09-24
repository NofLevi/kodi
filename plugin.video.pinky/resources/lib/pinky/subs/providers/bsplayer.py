"""BSPlayer, which answers only to a file hash - and is very good at it.

It is the one anonymous provider that matches on the file rather than on the
name. No account, no key: `logIn` with an empty username and password returns a
token. Measured on 9 September 2026:

    logIn                                     200 / OK, token returned
    searchSubtitles by hash                   100 results, Hebrew and English,
                                              exact release names
    searchSubtitles with an empty hash        HTTP 500

That last line is the shape of the whole provider. It is **hash-only**: there
is no title search and no IMDb fallback, so it contributes nothing before
playback starts and everything once a stream is open. That is the right way
round - a hash match is the only evidence that a subtitle belongs to *this
file* rather than to something with the same name, and the chooser already
scores one at 100.

So it is asked alongside the others and simply returns nothing when there is no
hash, rather than being conditionally enabled somewhere the viewer cannot see.

The transport is SOAP, which is why this file is longer than it deserves to be.
Two details cost a probe each and are worth keeping: the response tags carry
`xsi:type` attributes, so `<data>` does not match and `<data[^>]*>` does; and
the service asks that a client identify itself, so the User-Agent is not
optional decoration.
"""
import re
import time

from ... import http, kodi
from . import common

NAME = "bsplayer"

# The service publishes a pool of numbered hosts and expects a client to pick
# one. They are equivalent; trying the next one is what a client does when one
# is having a bad day.
HOSTS = ["http://s%d.api.bsplayer-subtitles.com/v1.php" % n for n in (1, 2, 3)]

USER_AGENT = "BSPlayer/2.x (1022.12360)"
APP_ID = "BSPlayer v2.72"

NAMESPACE = ("xmlns:SOAP-ENV=\"http://schemas.xmlsoap.org/soap/envelope/\" "
             "xmlns:ns1=\"http://api.bsplayer-subtitles.com/v1.php\"")

# ISO 639-2, as the API speaks it.
THREE_LETTER = {
    "he": "heb", "en": "eng", "ar": "ara", "ru": "rus", "es": "spa",
    "fr": "fre", "de": "ger", "it": "ita", "pt": "por", "tr": "tur",
    "pl": "pol", "nl": "dut", "ro": "rum", "cs": "cze", "hu": "hun",
    "uk": "ukr", "ja": "jpn", "ko": "kor", "zh": "chi", "hi": "hin",
}

TWO_LETTER = dict((three, two) for two, three in THREE_LETTER.items())

MAX_RESULTS = 20

# The whole of one SOAP call, hosts and retries included. The subtitle search
# runs on a 10 second deadline through four shared worker threads, so a
# provider is allowed a little longer than that and no more.
# Six, not twelve. The search deadline is ten seconds, so a twelve second
# budget meant one call could outlast the whole search - and it did: this
# provider is what `deadline hit after 10.0s, dropped: ktuvit` was really
# about, a worker spending longer than the deadline on hosts that never
# answer, with whatever queued behind it dropped too.
MAX_CALL_SECONDS = 6.0

# Attributes on every tag, so a bare <tag> pattern silently matches nothing.
_FIELD = r"<%s[^>]*>([^<]*)</%s>"
_ROW = re.compile(r"<item[^>]*>(.*?)</item>", re.S)


# Said once per process, because the alternative is a provider that is
# switched on and silently contributes nothing - the same thing SubSource
# does and for the same reason.
_SAID = []


def _answering():
    """Whether it is worth asking. Measured 24 September 2026: it is not.

    All three hosts resolve to one address, 185.100.234.211, and none of them
    accepts a connection on port 80 - a four second timeout each, three of
    them, for every search. That is not a bad day: the service is gone.

    It therefore ships **off**, and this exists for anyone who switches it on
    and wonders why nothing changed. The check is a log line rather than a
    probe, because probing to find out that probing does not work is the cost
    this is here to avoid.
    """
    if not _SAID:
        _SAID.append(True)
        kodi.log("bsplayer is switched on, but its hosts stopped answering in "
                 "September 2026 - all three resolve to one address that "
                 "accepts no connection. Nothing will come back.")
    return True


def supports(language):
    return language in THREE_LETTER


def _field(name, text):
    found = re.search(_FIELD % (name, name), text)
    return found.group(1).strip() if found else ""


def _call(action, body, timeout=(5, 15)):
    """One SOAP call, trying the hosts in turn. Returns the body or ""."""
    envelope = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<SOAP-ENV:Envelope %s><SOAP-ENV:Body>"
                "<ns1:%s>%s</ns1:%s>"
                "</SOAP-ENV:Body></SOAP-ENV:Envelope>"
                % (NAMESPACE, action, body, action))
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "\"http://api.bsplayer-subtitles.com/v1.php#%s\"" % action,
    }
    # Under a wall-clock budget, because the worker slot is the scarce thing.
    # Three hosts, each retried once at a 15 second read timeout, is up to 120
    # seconds for one call and 240 for a search - on one of the four threads
    # the whole add-on shares. The search deadline can abandon the *result*
    # but cannot stop a running task, so without this the source search and
    # every other provider queue behind a service that is simply down.
    deadline = time.time() + MAX_CALL_SECONDS
    for host in HOSTS:
        if time.time() >= deadline:
            kodi.log("bsplayer took too long, giving up on the rest")
            break
        response = http.post(host, data=envelope.encode("utf-8"),
                             headers=headers, timeout=timeout, retries=0)
        if response is not None and response.status_code == 200:
            try:
                return response.text or ""
            except LookupError:
                # A charset the server named and Python does not have.
                return ""
    return ""


def _token():
    """An anonymous session. Empty credentials are what the service expects."""
    body = _call("logIn", "<username></username><password></password>"
                          "<AppID>%s</AppID>" % APP_ID)
    if not body:
        return ""
    return _field("data", body)


def search(meta, target, languages, video_hash="", video_size=0):
    """Subtitles for this exact file, or nothing.

    No hash means no question worth asking - an empty one is an HTTP 500, not
    an empty result - so this returns early rather than spending a request to
    find that out.
    """
    if not _answering():
        return []
    if not video_hash or not video_size:
        return []
    codes = [THREE_LETTER[language] for language in languages
             if language in THREE_LETTER]
    if not codes:
        return []

    token = _token()
    if not token:
        kodi.log("bsplayer did not grant a session")
        return []

    body = _call("searchSubtitles",
                 "<handle>%s</handle><movieHash>%s</movieHash>"
                 "<movieSize>%s</movieSize><languageId>%s</languageId>"
                 "<imdbId></imdbId>"
                 % (token, video_hash, video_size, ",".join(codes)))
    if not body:
        return []
    if _field("status", body) not in ("OK", ""):
        return []

    results = []
    for chunk in _ROW.findall(body)[:MAX_RESULTS]:
        link = _field("subDownloadLink", chunk)
        language = TWO_LETTER.get(_field("subLang", chunk))
        if not link or not language:
            continue
        results.append(common.candidate(
            NAME, language, _field("subName", chunk), link,
            downloads=_number(_field("subDownloadsCnt", chunk)),
            # Everything here matched the file itself. That is the strongest
            # evidence a candidate can carry and the reason this provider is
            # worth its SOAP.
            hash_match=True,
        ))
    return results


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def download(candidate):
    """The links are gzipped, as OpenSubtitles' are."""
    data = common.fetch_bytes(candidate["download"],
                              headers={"User-Agent": USER_AGENT})
    if not data:
        return b""
    if data[:2] != b"\x1f\x8b":
        return common.extract_subtitle(data, candidate.get("language", ""),
                                       candidate=candidate)
    return common.decompress_gzip(data)
