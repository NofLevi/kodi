"""The OpenSubtitles file hash, computed over a debrid stream.

This is the strongest signal there is. A hash match means the subtitle was
uploaded against a byte-identical copy of the file being played, so the timings
are right by construction and no name guessing is involved.

The trick is that we never have the file locally. Debrid links are ordinary
HTTP with range support, so the hash is computed from a HEAD for the size and
two 64 KB range requests. That is about 130 KB of traffic, which is nothing
next to downloading a subtitle for the wrong release and having to try again.
"""
import re
import struct

from .. import http, kodi

CHUNK = 65536
LONG_SIZE = 8
LONGS_PER_CHUNK = CHUNK // LONG_SIZE
MASK64 = 0xFFFFFFFFFFFFFFFF

# Below this the algorithm is not defined, and such a file is not a video.
MIN_SIZE = CHUNK * 2
_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.I)


def hash_stream(url, timeout=(4, 8)):
    """Return (hash, size) for a remote file, or ("", 0) when unavailable."""
    if not url or not url.startswith("http"):
        return "", 0

    size = _remote_size(url, timeout)
    if size < MIN_SIZE:
        return "", 0

    head = _range(url, 0, CHUNK - 1, timeout, size)
    if head is None or len(head) < CHUNK:
        return "", 0
    tail = _range(url, size - CHUNK, size - 1, timeout, size)
    if tail is None or len(tail) < CHUNK:
        return "", 0

    return compute(size, head, tail), size


def compute(size, head, tail):
    """The published algorithm: file size plus the sum of the first and last
    64 KB read as unsigned 64 bit little-endian integers."""
    value = size & MASK64
    for chunk in (head, tail):
        for offset in range(LONGS_PER_CHUNK):
            (number,) = struct.unpack_from("<q", chunk, offset * LONG_SIZE)
            value = (value + number) & MASK64
    return "%016x" % value


def hash_local(path):
    """Same hash for a file already on disk, used for local playback."""
    import os
    try:
        size = os.path.getsize(path)
        if size < MIN_SIZE:
            return "", 0
        with open(path, "rb") as handle:
            head = handle.read(CHUNK)
            handle.seek(-CHUNK, os.SEEK_END)
            tail = handle.read(CHUNK)
    except (OSError, IOError):
        return "", 0
    if len(head) < CHUNK or len(tail) < CHUNK:
        return "", 0
    return compute(size, head, tail), size


def _remote_size(url, timeout):
    """Content length, from HEAD or from a one byte ranged GET as a fallback."""
    response = http.request("HEAD", url, retries=0, timeout=timeout,
                            allow_redirects=True)
    if response is not None and response.status_code < 400:
        length = response.headers.get("Content-Length")
        _discard(response)
        if length and length.isdigit():
            return int(length)
    elif response is not None:
        _discard(response)

    response = http.get(url, retries=0, timeout=timeout,
                        headers={"Range": "bytes=0-0"}, stream=True)
    if response is None:
        return 0
    try:
        content_range = response.headers.get("Content-Range", "")
        if "/" in content_range:
            total = content_range.rsplit("/", 1)[-1]
            if total.isdigit():
                return int(total)
    finally:
        _discard(response)
    return 0


def _range(url, start, end, timeout, expected_size=None):
    response = http.get(
        url, retries=1, timeout=timeout, stream=True,
        headers={"Range": "bytes=%d-%d" % (start, end),
                 "Accept-Encoding": "identity"})
    if response is None:
        return None
    try:
        # A 200 means the server ignored Range and may be streaming a complete
        # multi-gigabyte movie. Never touch .content in that case: both HTTP
        # backends materialise the entire body before returning it.
        if response.status_code != 206:
            return None
        match = _CONTENT_RANGE.match(
            (response.headers.get("Content-Range") or "").strip())
        if not match or int(match.group(1)) != start or int(match.group(2)) != end:
            return None
        total = match.group(3)
        if total == "*" or (expected_size is not None
                             and int(total) != int(expected_size)):
            return None
        expected = end - start + 1
        raw = getattr(response, "raw", None)
        if raw is None:
            return None
        try:
            data = raw.read(expected + 1, decode_content=False)
        except TypeError:
            data = raw.read(expected + 1)
        return data if len(data) == expected else None
    except Exception:
        kodi.log_exception("range request failed")
        return None
    finally:
        _discard(response)


def _discard(response):
    try:
        response.close()
    except Exception:
        pass
