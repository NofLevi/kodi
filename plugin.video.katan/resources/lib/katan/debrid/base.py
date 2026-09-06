"""The interface every debrid service implements.

The four services disagree about almost everything: how you sign in, whether
they will tell you what is cached, whether you pick files or they take the
whole torrent. This class hides that so the aggregator can ask one question,
"which of these hashes can you play right now", and get one answer.
"""
from .. import cache, kodi, settings

# Video containers worth playing. Everything else in a torrent is noise.
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".m2ts", ".wmv")

# Files smaller than this are samples, trailers or extras, never the feature.
MIN_VIDEO_BYTES = 80 * 1024 * 1024

CACHE_TTL = 3600        # how long a cached/not-cached answer is trusted


class DebridService(object):
    name = ""
    label = ""

    # -- credentials -------------------------------------------------------

    def configured(self):
        raise NotImplementedError

    def authorize(self):
        """Run the sign-in flow. Returns True on success."""
        raise NotImplementedError

    def account_info(self):
        """A dict describing the account, or None."""
        return None

    # -- the two operations that matter ------------------------------------

    def is_cached(self, hashes):
        """Map of infohash -> bool. Unknown hashes may be omitted."""
        raise NotImplementedError

    def resolve(self, source):
        """Return a playable URL for a source, or an empty string."""
        raise NotImplementedError

    # -- shared helpers ----------------------------------------------------

    def cached_with_memory(self, hashes):
        """is_cached, but reusing recent answers.

        Cache status barely changes minute to minute, and every service rate
        limits, so remembering the last answer is what keeps a source search
        inside its deadline.
        """
        hashes = [h for h in hashes if h]
        if not hashes:
            return {}

        known = {}
        unknown = []
        for info_hash in hashes:
            hit = cache.get(self._cache_key(info_hash))
            if hit is None:
                unknown.append(info_hash)
            else:
                known[info_hash] = bool(hit.get("cached"))

        if unknown:
            try:
                fresh = self.is_cached(unknown) or {}
            except Exception:
                kodi.log_exception("%s cache check failed" % self.name)
                fresh = {}
            for info_hash in unknown:
                if info_hash in fresh:
                    known[info_hash] = bool(fresh[info_hash])
                    cache.set(self._cache_key(info_hash),
                              {"cached": bool(fresh[info_hash])}, CACHE_TTL)
        return known

    def _cache_key(self, info_hash):
        return cache.make_key("debrid", self.name, info_hash)

    def forget(self, info_hash=None):
        if info_hash:
            cache.delete(self._cache_key(info_hash))
        else:
            cache.delete_prefix("debrid|%s|" % self.name)

    # -- file selection ----------------------------------------------------

    def pick_file(self, files, source, meta=None):
        """Choose the video file to play out of a torrent.

        files is a list of dicts with at least "name" and "size", optionally
        "id" or "index". Season packs are the reason this exists: the torrent
        holds a whole series and only one file is the episode asked for.
        """
        from ..utils import release

        candidates = []
        for position, entry in enumerate(files or []):
            name = (entry.get("name") or entry.get("path") or "")
            size = int(entry.get("size") or entry.get("bytes") or 0)
            if not name.lower().endswith(VIDEO_EXTENSIONS):
                continue
            if size and size < MIN_VIDEO_BYTES:
                continue
            if _is_extra(name):
                continue
            candidates.append({
                "id": entry.get("id", entry.get("index", position)),
                "index": position,
                "name": name,
                "size": size,
            })

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        meta = meta or {}
        if meta.get("type") == "episode":
            season = int(meta.get("season") or 0)
            episode = int(meta.get("episode") or 0)
            matches = [c for c in candidates
                       if release.matches_episode(
                           release.parse(_basename(c["name"])), season, episode)]
            if matches:
                return max(matches, key=lambda c: c["size"])
            kodi.log("no file in the pack matched S%02dE%02d" % (season, episode))
            return None                 # better to fail than play the wrong episode

        return max(candidates, key=lambda c: c["size"])


def _basename(path):
    return path.replace("\\", "/").rsplit("/", 1)[-1]


_EXTRA_WORDS = ("sample", "trailer", "featurette", "extras", "behind the scenes",
                "deleted scene", "bloopers", "rarbg", "proof")


def _is_extra(name):
    lowered = _basename(name).lower()
    return any(word in lowered for word in _EXTRA_WORDS)


def timeout_for(operation):
    """Per-operation timeouts. Cache checks must never hold up a search."""
    return {
        "cache": (4, 6),
        "resolve": (5, 20),
        "auth": (5, 15),
    }.get(operation, (5, 10))
