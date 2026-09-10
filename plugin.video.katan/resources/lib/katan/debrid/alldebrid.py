"""AllDebrid.

Sign-in is a PIN flow: ask for a PIN, the user types it at alldebrid.com/pin,
we poll until it activates and receive a permanent API key. Documented limits
are 12 requests a second and 600 a minute, which is generous, but cache answers
are still remembered so a source search does not spend them.

There is no bulk instant-availability endpoint, so cached status comes from the
ready flag returned when a magnet is uploaded.
"""
import time

from .. import http, kodi, settings
from . import base

API = "https://api.alldebrid.com/v4"
API_41 = "https://api.alldebrid.com/v4.1"
AGENT = "katan"


class AllDebrid(base.DebridService):
    name = "alldebrid"
    label = "AllDebrid"

    def key(self):
        return settings.get("alldebrid.apikey").strip()

    def configured(self):
        return bool(self.key())

    def _params(self, **extra):
        params = {"agent": AGENT, "apikey": self.key()}
        params.update(extra)
        return params

    # -- authorisation -----------------------------------------------------

    methods = ("scan", "key")
    key_url = "https://alldebrid.com/apikeys"
    key_setting = "alldebrid.apikey"

    def credential_settings(self):
        return ["alldebrid.apikey"]

    def authorize(self, method=None):
        from ..ui import signin

        if method == "key":
            previous = self.key()
            entered = signin.ask_for_key("%s API key" % self.label, previous,
                                         help_url=self.key_url)
            if entered is None:
                return False
            settings.set("alldebrid.apikey", entered)
            if self.account_info():
                return True
            settings.set("alldebrid.apikey", previous)
            return False

        start = http.get_json("%s/pin/get" % API_41, params={"agent": AGENT},
                              timeout=base.timeout_for("auth"), default=None)
        data = (start or {}).get("data")
        if not data:
            return False

        def poll():
            payload = http.get_json("%s/pin/check" % API,
                                    params={"agent": AGENT,
                                            "check": data.get("check"),
                                            "pin": data.get("pin")},
                                    default=None)
            found = (payload or {}).get("data") or {}
            if found.get("activated") and found.get("apikey"):
                settings.set("alldebrid.apikey", found["apikey"])
                return True
            return False

        # AllDebrid's own page is the one to scan: it carries the PIN in the
        # URL, so scanning it skips typing the PIN as well as the address.
        return signin.run_device(self.label, data.get("user_url", ""),
                                 data.get("pin", ""), poll,
                                 lifetime=data.get("expires_in"), interval=4)

    def account_info(self):
        if not self.configured():
            return None
        payload = http.get_json("%s/user" % API, params=self._params(),
                                timeout=base.timeout_for("auth"), default=None)
        user = ((payload or {}).get("data") or {}).get("user")
        if not user:
            return None
        return {
            "user": user.get("username", ""),
            "plan": "premium" if user.get("isPremium") else "free",
            "expires": user.get("premiumUntil", ""),
            "is_free": not user.get("isPremium"),
        }

    # -- cache -------------------------------------------------------------

    def is_cached(self, hashes):
        """AllDebrid has no bulk cache endpoint, so we cannot answer cheaply.

        Uploading every candidate to read its ready flag would be an abuse of
        the API, so we say nothing and let the indexer flags stand.
        """
        return {}

    # -- playback ----------------------------------------------------------

    def resolve(self, source):
        if not self.configured():
            return ""
        from ..sources import model

        magnet = model.magnet_for(source)
        if not magnet:
            return ""

        uploaded = http.post_json("%s/magnet/upload" % API,
                                  params=self._params(),
                                  data={"magnets[]": magnet},
                                  timeout=base.timeout_for("resolve"), default=None)
        magnets = ((uploaded or {}).get("data") or {}).get("magnets") or []
        if not magnets:
            return ""
        entry = magnets[0]
        magnet_id = entry.get("id")
        if not magnet_id:
            return ""

        allow_uncached = source.get("extra", {}).get("allow_uncached", False)
        if not entry.get("ready") and not allow_uncached:
            kodi.log("AllDebrid source is not ready, dropping it")
            self._delete(magnet_id)
            return ""

        status = self._wait_ready(magnet_id, allow_uncached)
        if not status:
            return ""

        files = [{"id": position, "name": link.get("filename", ""),
                  "size": int(link.get("size") or 0), "link": link.get("link", "")}
                 for position, link in enumerate(status.get("links") or [])]
        chosen = self.pick_file(files, source, source.get("extra", {}).get("meta"))
        if not chosen:
            return ""
        return self._unlock(files[chosen["index"]].get("link", ""), source, chosen)

    def _wait_ready(self, magnet_id, allow_uncached, attempts=5):
        for attempt in range(attempts):
            payload = http.get_json("%s/magnet/status" % API_41,
                                    params=self._params(id=magnet_id),
                                    timeout=base.timeout_for("resolve"),
                                    default=None)
            status = ((payload or {}).get("data") or {}).get("magnets")
            if isinstance(status, list):
                status = status[0] if status else None
            if not status:
                return None
            if status.get("statusCode") == 4:       # Ready
                return status
            if status.get("statusCode", 0) > 4:     # error states
                self._delete(magnet_id)
                return None
            if not allow_uncached and attempt >= 1:
                self._delete(magnet_id)
                return None
            time.sleep(2)
        return None

    def _unlock(self, link, source, chosen):
        if not link:
            return ""
        payload = http.get_json("%s/link/unlock" % API,
                                params=self._params(link=link),
                                timeout=base.timeout_for("resolve"), default=None)
        url = ((payload or {}).get("data") or {}).get("link", "")
        if url:
            base.record_selection(source, chosen)
        return url

    def _delete(self, magnet_id):
        try:
            http.get("%s/magnet/delete" % API, params=self._params(id=magnet_id),
                     retries=0)
        except Exception:
            pass
