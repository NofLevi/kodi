"""TorBox.

Verified against the official API docs: a static API key sent as a Bearer
header, 300 requests per minute per token, and a separate cap of 60 uncached
torrent creations per hour. That last limit shapes the design: we pass
add_only_if_cached for normal playback and only spend an uncached create when
the user explicitly asks for something that is not ready yet.

TorBox also has no file selection step, because it always downloads every file
in a torrent. File picking therefore happens on our side, after the fact.
"""
from .. import http, kodi, settings
from . import base

API = "https://api.torbox.app/v1/api"

# The GET form is limited by URL length; the docs put it at roughly 100.
CHECK_BATCH = 90


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

    def authorize(self):
        entered = kodi.keyboard(self.key(), "TorBox API key")
        if entered is None:
            return False
        settings.set("torbox.apikey", entered.strip())
        info = self.account_info()
        if info:
            kodi.log("TorBox plan: %s" % info.get("plan"))
            return True
        settings.set("torbox.apikey", "")
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
        torrent = self._existing(source["hash"]) or self._create(source)
        if not torrent:
            return ""

        chosen = self.pick_file(torrent.get("files") or [], source,
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

    def _existing(self, info_hash):
        """Is this torrent already in the account, from a previous play?"""
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
        from . import registry
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
        return self._by_id(torrent_id)

    def _by_id(self, torrent_id):
        payload = http.get_json("%s/torrents/mylist" % API,
                                headers=self._headers(),
                                params={"id": torrent_id, "bypass_cache": "true"},
                                timeout=base.timeout_for("cache"), default=None)
        data = (payload or {}).get("data")
        if isinstance(data, list):
            return data[0] if data else None
        return data


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
