"""TorBox.

Verified against the official API docs: a static API key sent as a Bearer
header, 300 requests per minute per token, and a separate cap of 60 uncached
torrent creations per hour. That last limit shapes the design: we pass
add_only_if_cached for normal playback and only spend an uncached create when
the user explicitly asks for something that is not ready yet.

TorBox also has no file selection step, because it always downloads every file
in a torrent. File picking therefore happens on our side, after the fact.
"""
import calendar
import time

from .. import http, kodi, settings
from . import base

API = "https://api.torbox.app/v1/api"

# The device flow TorBox added for TV apps. Measured against the live service
# rather than taken from the documentation, because the OpenAPI document
# publishes no schema at all for either success response:
#
#   GET  /user/auth/device/start?app=Katan
#     -> data: device_code, code ("540608"), interval (5), expires_at (ISO
#        8601 with a Z), verification_url, friendly_verification_url
#   POST /user/auth/device/token  {"device_code": ...}
#     -> 200 once approved; 400 with error DEVICE_CODE_NOT_USED while it has
#        not been, and 400 with ITEM_NOT_FOUND once it is invalid or expired.
#
# Both waiting and dead are HTTP 400, so the error name is the only thing that
# separates "keep the screen up" from "this code will never work" - and that
# distinction is the whole reason the poll has three answers instead of two.
DEVICE_START = API + "/user/auth/device/start"
DEVICE_TOKEN = API + "/user/auth/device/token"
DEVICE_URL = "https://torbox.app/oauth/device"
APP_NAME = "Katan"

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

    # TorBox used to be the one service with no device flow, so its "scan"
    # meant scanning a link to the page where the key lives and then typing
    # thirty-two characters on a remote anyway. It has a real one now.
    methods = ("scan", "key")
    key_url = "https://torbox.app/settings"
    key_setting = "torbox.apikey"

    def credential_settings(self):
        return ["torbox.apikey"]

    def authorize(self, method=None):
        if method == "key":
            return self._authorize_with_typed_key()
        return self._device_flow()

    def _authorize_with_typed_key(self):
        from ..ui import signin

        previous = self.key()
        entered = signin.ask_for_key("%s API key" % self.label, previous,
                                     help_url=self.key_url)
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

    def _device_flow(self):
        from ..ui import signin

        start = http.get_json(DEVICE_START, params={"app": APP_NAME},
                              timeout=base.timeout_for("auth"), default=None)
        data = (start or {}).get("data") or {}
        if not data.get("device_code"):
            return False

        def poll():
            # Not post_json: it turns every status past 400 into the default,
            # and here the body of a 400 is the entire answer.
            response = http.post(DEVICE_TOKEN,
                                 json={"device_code": data["device_code"]})
            if response is None:
                return False                  # a blip, not an answer
            if response.status_code == 200:
                return self._keep_device_token(response)
            try:
                error = (response.json() or {}).get("error")
            except ValueError:
                return False
            return False if error == "DEVICE_CODE_NOT_USED" else None

        return signin.run_device(
            self.label, data.get("verification_url", DEVICE_URL),
            data.get("code", ""), poll,
            lifetime=_seconds_until(data.get("expires_at")),
            interval=max(4, int(data.get("interval") or 5)))

    def _keep_device_token(self, response):
        """Store whatever the approved token came back as.

        The 200 has no schema in TorBox's own OpenAPI document, and this is
        the service whose `requestdl` answers with a bare string where
        everything else answers with an object. So take a string if that is
        what arrives and otherwise look under each name it could reasonably
        use, rather than picking one and finding out on somebody's projector.

        Returns True, or None - never False. A token that does not work is
        the end of this attempt, not a reason to keep the screen up.
        """
        try:
            payload = response.json() or {}
        except ValueError:
            return None
        data = payload.get("data")
        token = data if isinstance(data, str) else ""
        if isinstance(data, dict):
            for name in ("token", "api_key", "apikey", "access_token",
                         "auth_token", "user_api_key"):
                if data.get(name):
                    token = str(data[name])
                    break
        token = (token or "").strip()
        if not token:
            kodi.log("TorBox approved the device but the token was not where "
                     "it was looked for: %s"
                     % (sorted(data) if isinstance(data, dict) else type(data)))
            return None
        previous = self.key()
        settings.set("torbox.apikey", token)
        info = self.account_info()
        if info:
            kodi.log("TorBox plan: %s" % info.get("plan"))
            return True
        settings.set("torbox.apikey", previous)
        kodi.log("TorBox handed back a token its own account call refused")
        return None

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
            base.record_selection(source, chosen)
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


def _seconds_until(stamp, fallback=600):
    """`expires_at` is a moment; the sign-in screen wants a duration.

    Clamped at both ends: a clock that disagrees with TorBox's must not
    produce a screen that closes at once or one that never closes.
    """
    if not stamp:
        return fallback
    text = str(stamp).split(".")[0].rstrip("Z") + "Z"
    try:
        expires = calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return fallback
    return max(60, min(1800, int(expires - time.time())))
