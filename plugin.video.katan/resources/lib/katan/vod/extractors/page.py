"""Shared page fetching and stream extraction for the Israeli broadcasters.

These sites are ordinary web pages that were never meant to be read by a media
player, and they change without notice. Rather than one brittle pattern per
site, each extractor tries a short ladder of strategies and reports which one
worked, so a breakage shows up in the log as "strategy X stopped matching"
instead of an empty screen with no explanation.

The strategies, most reliable first:

    ld+json      a schema.org VideoObject carries a contentUrl
    m3u8         an HLS manifest appears directly in the markup
    youtube      the page embeds YouTube, so hand off to the YouTube add-on
    kaltura      a Kaltura partner and entry id, which builds a known URL
"""
import json
import re

from ... import http, kodi

# These sites reject anything that does not look like a browser.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
}

YOUTUBE_PLUGIN = "plugin://plugin.video.youtube/play/?video_id=%s"

_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S)
_M3U8 = re.compile(r'["\'](https?://[^"\']+\.m3u8[^"\']*)["\']')
_MPD = re.compile(r'["\'](https?://[^"\']+\.mpd[^"\']*)["\']')
_YOUTUBE = re.compile(
    r'(?:youtube\.com/(?:watch\?v=|embed/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})')
_KALTURA_PARTNER = re.compile(r'partner[_ ]?id["\']?\s*[:=]\s*["\']?(\d{3,8})', re.I)
_KALTURA_ENTRY = re.compile(r'entry[_ ]?id["\']?\s*[:=]\s*["\']?(0_[a-z0-9]+|1_[a-z0-9]+)', re.I)


def fetch(url, referer="", timeout=(6, 15)):
    """Fetch a page as a browser would. Returns the text, or an empty string."""
    if not url or not url.startswith("http"):
        return ""
    headers = dict(BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer
    response = http.get(url, headers=headers, timeout=timeout, retries=1)
    if response is None:
        return ""
    if response.status_code >= 400:
        kodi.log("%s returned HTTP %s" % (_host(url), response.status_code))
        return ""
    try:
        return response.text
    except Exception:
        return ""


def fetch_json(url, referer="", timeout=(6, 15), params=None):
    headers = dict(BROWSER_HEADERS)
    headers["Accept"] = "application/json, text/plain, */*"
    if referer:
        headers["Referer"] = referer
    return http.get_json(url, headers=headers, params=params,
                         timeout=timeout, default=None)


def _host(url):
    try:
        return url.split("/")[2]
    except IndexError:
        return url


# --------------------------------------------------------------------------
# extraction strategies
# --------------------------------------------------------------------------


def ld_json_blocks(html):
    """Every schema.org block on a page, parsed."""
    blocks = []
    for raw in _LD_JSON.findall(html or ""):
        try:
            parsed = json.loads(raw.strip())
        except ValueError:
            continue
        blocks.extend(parsed if isinstance(parsed, list) else [parsed])
    return blocks


def video_from_ld_json(html):
    """A contentUrl from a VideoObject, which is the cleanest possible answer."""
    for block in ld_json_blocks(html):
        if not isinstance(block, dict):
            continue
        if block.get("@type") not in ("VideoObject", "Movie", "TVEpisode"):
            continue
        for key in ("contentUrl", "embedUrl"):
            url = block.get(key)
            if url and (".m3u8" in url or ".mp4" in url or ".mpd" in url):
                return url
    return ""


def manifest_from_html(html):
    """The first HLS or DASH manifest that appears in the markup."""
    match = _M3U8.search(html or "")
    if match:
        return match.group(1).replace("\\/", "/")
    match = _MPD.search(html or "")
    if match:
        return match.group(1).replace("\\/", "/")
    return ""


def youtube_id(html_or_url):
    match = _YOUTUBE.search(html_or_url or "")
    return match.group(1) if match else ""


def kaltura_url(html):
    """Build a Kaltura HLS URL from a partner and entry id."""
    partner = _KALTURA_PARTNER.search(html or "")
    entry = _KALTURA_ENTRY.search(html or "")
    if not (partner and entry):
        return ""
    return ("https://cdnapisec.kaltura.com/p/%s/sp/%s00/playManifest/entryId/%s"
            "/format/applehttp/protocol/https/a.m3u8"
            % (partner.group(1), partner.group(1), entry.group(1)))


def extract_stream(html, page_url=""):
    """Run the ladder and return (url, is_adaptive, strategy)."""
    for name, finder in (("ld+json", video_from_ld_json),
                         ("m3u8", manifest_from_html),
                         ("kaltura", kaltura_url)):
        url = finder(html)
        if url:
            return url, url.endswith(".mpd") or ".mpd?" in url, name

    video = youtube_id(html) or youtube_id(page_url)
    if video:
        return YOUTUBE_PLUGIN % video, False, "youtube"

    return "", False, ""


def absolute(url, base):
    """Turn a relative href into a full URL."""
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    root = "/".join(base.split("/")[:3]) if base.startswith("http") else ""
    if url.startswith("/"):
        return root + url
    return base.rstrip("/") + "/" + url
