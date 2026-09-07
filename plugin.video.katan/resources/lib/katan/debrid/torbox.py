"""TorBox.

Verified against the official API docs: a static API key sent as a Bearer
header, 300 requests per minute per token, and a separate cap of 60 uncached
torrent creations per hour. That last limit shapes the design: we pass
add_only_if_cached for normal playback and only spend an uncached create when
the user explicitly asks for something that is not ready yet.

TorBox also has no file selection step, because it always downloads every file
in a torrent. File picking therefore happens on our side, after the fact.
"""
import time

from .. import http, kodi, settings
from . import base

API = "https://api.torbox.app/v1/api"

# The GET form is limited by URL length; the docs put it at roughly 100.
CHECK_BATCH = 90

# A torrent that has just been added can come back before its file list does.
# Bounded on purpose: this runs between the viewer pressing play and the
# picture appearing.
FILES_ATTEMPTS = 4
FILES_DELAY = 1.0


class TorBox(base.DebridService):
    name = "torbox"
    label = "TorBox"

    def key(self):
        return settings.get("torbox.apikey").strip()

    def configured(self):
        return bool(self.key())

    def _headers(self):
        return {"Authorization": "Bearer %s" % self.key()}

    # -- account -----------------------------------------------------------

    # TorBox has no device flow at all - the key from its settings page is
    # the only way in - so "scan" here means scanning a link to that page,
    # which is honest about what it does and still saves finding it by hand.
    methods = ("scan", "key")
    key_url = "https://torbox.app/settings"

    def credential_settings(self):
        return ["torbox.apikey"]

    def authorize(self, method=None):
        from ..ui import signin

        previous = self.key()
        entered = signin.ask_for_key(
            "%s API key" % self.label, previous,
            help_url=self.key_url if method == "scan" else "")
        if entered is None:
            return False
        settings.set("torbox.apikey", entered)
        info = self.account_info()
        if info:
            kodi.log("TorBox plan: %s" % info.get("plan"))
            return True
        # Put back whatever was working before rather than leaving the viewer
        # signed out because they mistyped a replacement key.
        settings.set("torbox.apikey", previous)
        return False

    def account_info(self):
        if not self.configured():
            return None
        payload = http.get_json("%s/user/me" % API, headers=self._headers(),
                                params={"settings": "false"},
                                timeout=base.timeout_for("auth"), default=None)
        data = (payload or {}).get("data")
        if not data:
            return None
        return {
            "user": data.get("email", ""),
            "plan": _PLAN_NAMES.get(data.get("plan"), str(data.get("plan"))),
            "expires": data.get("premium_expires_at", ""),
            "is_free": data.get("plan") in (0, None),
        }

    # -- cache -------------------------------------------------------------

    def is_cached(self, hashes):
        if not self.configured() or not hashes:
            return {}
        result = {}
        for batch in _chunks(list(hashes), CHECK_BATCH):
            payload = http.get_json(
                "%s/torrents/checkcached" % API,
                headers=self._headers(),
                params={"hash": ",".join(batch), "format": "list"},
                timeout=base.timeout_for("cache"),
                default=None)
            if payload is None:
                continue
            found = _cached_hashes(payload.get("data"))
            for info_hash in batch:
                result[info_hash] = info_hash.lower() in found
        return result

    # -- playback ----------------------------------------------------------

    def resolve(self, source):
        if not self.configured():
            return ""
        torrent = self._find(source)
        if not torrent:
            return ""

        if not torrent.get("files"):
            # Not the same thing as "no usable video file", which is what this
            # used to say. An indexer marks a source cached and TorBox accepts
            # the magnet, and it can still turn out that TorBox does not have
            # it: state "downloading", cached false, no file list. There is
            # nothing to pick from because there is nothing there yet.
            kodi.log("TorBox is still fetching %s (state %r), so there is "
                     "nothing to play yet"
                     % (source.get("hash", "")[:12],
                        torrent.get("download_state")))
            return ""

        chosen = self.pick_file(torrent["files"], source,
                                source.get("extra", {}).get("meta"))
        if not chosen:
            kodi.log("TorBox torrent has no usable video file")
            return ""

        payload = http.get_json(
            "%s/torrents/requestdl" % API,
            params={"token": self.key(), "torrent_id": torrent.get("id"),
                    "file_id": chosen["id"], "redirect": "false"},
            timeout=base.timeout_for("resolve"), default=None)
        link = (payload or {}).get("data")
        if isinstance(link, dict):
            link = link.get("url") or link.get("link")
        if link:
            source["file_name"] = chosen["name"]
        return link or ""

    def _find(self, source):
        """The torrent to play, added if it is not already on the account.

        Which of the two calls goes first matters more than it looks, and the
        numbers are measured against a real account, not guessed:

            _existing   2.0 - 3.5 s, and 466 KB of JSON for 58 torrents. It
                        downloads the *whole account* to look for one hash, so
                        it gets slower every time anything is played.
            _create     0.46 s, and answers "Found Cached Torrent. Using Cached
                        Torrent." with the same torrent_id when the torrent is
                        already there.

        So adding a torrent you already have is not a mistake TorBox charges
        you for; it is the cheap way to ask "do I have this, and if not, take
        it". `_existing` stays as the fallback, because a torrent that finished
        downloading but is no longer *cached* is one `add_only_if_cached` would
        refuse and the account list would still find.

        The order flips when uncached adds are allowed, because then adding is
        not free: it spends one of sixty an hour. Look before leaping.
        """
        info_hash = source["hash"]
        if source.get("extra", {}).get("allow_uncached", False):
            return self._existing(info_hash) or self._create(source)
        return self._create(source) or self._existing(info_hash)

    def _existing(self, info_hash):
        """Is this torrent already in the account, from a previous play?

        Expensive: there is no way to ask TorBox about one hash, so this pulls
        the entire account list. See _find for why it is the second choice.
        """
        payload = http.get_json("%s/torrents/mylist" % API,
                                headers=self._headers(),
                                params={"bypass_cache": "true"},
                                timeout=base.timeout_for("cache"), default=None)
        for torrent in (payload or {}).get("data") or []:
            if (torrent.get("hash") or "").lower() == info_hash.lower():
                if torrent.get("download_finished") or torrent.get("cached"):
                    return torrent
        return None

    def _create(self, source):
        """Add the torrent, refusing an uncached add unless asked.

        Uncached creates are capped at 60 an hour, and a source search can
        easily consider more than that, so the flag is what protects the quota.
        """
        from ..sources import model

        magnet = model.magnet_for(source)
        if not magnet:
            return None
        allow_uncached = source.get("extra", {}).get("allow_uncached", False)
        data = {"magnet": magnet, "seed": 3}
        if not allow_uncached:
            data["add_only_if_cached"] = "true"

        response = http.post("%s/torrents/createtorrent" % API,
                             headers=self._headers(), data=data,
                             timeout=base.timeout_for("resolve"))
        if response is None:
            return None
        if response.status_code == 429:
            kodi.log_error("TorBox rate limit hit on createtorrent")
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not payload.get("success"):
            kodi.log("TorBox refused the torrent: %s" % payload.get("detail"))
            return None
        torrent_id = (payload.get("data") or {}).get("torrent_id")
        if not torrent_id:
            return None
        return self._await_files(torrent_id)

    def _await_files(self, torrent_id):
        """Fetch the torrent, waiting for its file list if it is not there yet.

        A torrent already on the account always comes back complete. One that
        has just been added does not always: createtorrent answers with an id
        the moment it accepts the magnet, and the file list can arrive a second
        or two later. Reading it too early looks exactly like a torrent with no
        video in it, and that is what "TorBox torrent has no usable video file"
        meant for two of five films the first time the account list stopped
        being fetched first.
        """
        torrent = self._by_id(torrent_id)
        for _attempt in range(FILES_ATTEMPTS - 1):
            if not torrent or torrent.get("files"):
                return torrent
            if not _claims_to_be_ready(torrent):
                # TorBox says it is downloading this one. Waiting will not
                # produce a file list in the second or two we have, and the
                # viewer is sitting in front of a black screen: fail now and
                # say so rather than adding three seconds to a dead end.
                return torrent
            time.sleep(FILES_DELAY)
            torrent = self._by_id(torrent_id)
        return torrent

    def _by_id(self, torrent_id):
        payload = http.get_json("%s/torrents/mylist" % API,
                                headers=self._headers(),
                                params={"id": torrent_id, "bypass_cache": "true"},
                                timeout=base.timeout_for("cache"), default=None)
        data = (payload or {}).get("data")
        if isinstance(data, list):
            return data[0] if data else None
        return data


def _claims_to_be_ready(torrent):
    """Does TorBox say it already has this, whatever the file list looks like?

    Worth separating from "has files", because the two disagree in both
    directions for a moment after a torrent is added, and only one of them is
    worth waiting on.
    """
    return bool(torrent.get("cached") or torrent.get("download_finished")
                or torrent.get("download_state") == "cached")


_PLAN_NAMES = {0: "Free", 1: "Essential", 2: "Standard", 3: "Pro"}


def _cached_hashes(data):
    """checkcached answers as a list or an object, depending on format."""
    found = set()
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                value = entry.get("hash")
                if value:
                    found.add(value.lower())
            elif isinstance(entry, str):
                found.add(entry.lower())
    elif isinstance(data, dict):
        for key, entry in data.items():
            if entry:
                found.add(key.lower())
    return found


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]
