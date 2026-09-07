"""Premiumize.

The friendliest of the four for a streaming client: cache/check takes a batch
of magnets and answers immediately, and transfer/directdl returns playable
links without ever storing anything in the account.

Authentication accepts either an API key sent as a Bearer token, or the OAuth
device flow for people who would rather not copy a key onto a TV box.
"""
import time

from .. import http, kodi, settings
from . import base

API = "https://www.premiumize.me/api"
TOKEN_URL = "https://www.premiumize.me/token"
CLIENT_ID = ""          # set in settings when using the device flow
CHECK_BATCH = 100


class Premiumize(base.DebridService):
    name = "premiumize"
    label = "Premiumize"

    def _credential(self):
        return (settings.get("premiumize.token")
                or settings.get("premiumize.apikey")).strip()

    def configured(self):
        return bool(self._credential())

    def _headers(self):
        return {"Authorization": "Bearer %s" % self._credential()}

    # -- authorisation -----------------------------------------------------

    methods = ("scan", "link", "key")
    key_url = "https://www.premiumize.me/account"

    def credential_settings(self):
        return ["premiumize.apikey", "premiumize.token"]

    def authorize(self, method=None):
        from ..ui import signin

        if method == "key":
            previous = settings.get("premiumize.apikey")
            entered = signin.ask_for_key("%s API key" % self.label, previous,
                                         help_url=self.key_url)
            if entered is None:
                return False
            settings.set("premiumize.apikey", entered)
            if self.account_info():
                return True
            settings.set("premiumize.apikey", previous)
            return False
        return self._device_flow(scan=(method != "link"))

    def _device_flow(self, scan=True):
        from ..ui import signin

        client_id = settings.get("premiumize.client_id")
        if not client_id:
            client_id = kodi.keyboard("", "Premiumize client id") or ""
            if not client_id.strip():
                return False
            settings.set("premiumize.client_id", client_id.strip())
            client_id = client_id.strip()

        start = http.post_json(TOKEN_URL, data={
            "client_id": client_id, "response_type": "device_code",
        }, timeout=base.timeout_for("auth"), default=None)
        if not start:
            return False

        # Premiumize asks to be polled more slowly when it says so, so the
        # interval has to be able to grow while the window is up.
        pace = {"interval": max(5, int(start.get("interval") or 5))}

        def poll():
            payload = http.post_json(TOKEN_URL, data={
                "client_id": client_id,
                "code": start.get("device_code"),
                "grant_type": "device_code",
            }, default=None)
            if not payload:
                return False
            if payload.get("access_token"):
                settings.set("premiumize.token", payload["access_token"])
                return True
            error = payload.get("error")
            if error == "slow_down":
                pace["interval"] += 1
                return False
            if error and error != "authorization_pending":
                kodi.log("Premiumize device flow stopped: %s" % error)
                return None
            return False

        return signin.run_device(self.label,
                                 start.get("verification_uri", ""),
                                 start.get("user_code", ""), poll,
                                 lifetime=start.get("expires_in"),
                                 interval=pace["interval"], scan=scan)

    def account_info(self):
        if not self.configured():
            return None
        payload = http.post_json("%s/account/info" % API, headers=self._headers(),
                                 timeout=base.timeout_for("auth"), default=None)
        if not payload or payload.get("status") != "success":
            return None
        return {
            "user": payload.get("customer_id", ""),
            "plan": "premium",
            "expires": payload.get("premium_until", ""),
            "is_free": not payload.get("premium_until"),
        }

    # -- cache -------------------------------------------------------------

    def is_cached(self, hashes):
        if not self.configured() or not hashes:
            return {}
        result = {}
        for batch in _chunks(list(hashes), CHECK_BATCH):
            payload = http.post_json(
                "%s/cache/check" % API, headers=self._headers(),
                data=[("items[]", "magnet:?xt=urn:btih:%s" % h) for h in batch],
                timeout=base.timeout_for("cache"), default=None)
            if not payload or payload.get("status") != "success":
                continue
            flags = payload.get("response") or []
            for position, info_hash in enumerate(batch):
                if position < len(flags):
                    result[info_hash] = bool(flags[position])
        return result

    # -- playback ----------------------------------------------------------

    def resolve(self, source):
        if not self.configured():
            return ""
        from ..sources import model

        magnet = model.magnet_for(source)
        if not magnet:
            return ""
        payload = http.post_json("%s/transfer/directdl" % API,
                                 headers=self._headers(),
                                 data={"src": magnet},
                                 timeout=base.timeout_for("resolve"), default=None)
        if not payload or payload.get("status") != "success":
            return ""

        files = [{"id": position, "name": entry.get("path") or entry.get("filename", ""),
                  "size": int(entry.get("size") or 0), "link": entry.get("link", "")}
                 for position, entry in enumerate(payload.get("content") or [])]
        chosen = self.pick_file(files, source, source.get("extra", {}).get("meta"))
        if not chosen:
            return ""
        link = files[chosen["index"]].get("link", "")
        if link:
            source["file_name"] = chosen["name"]
        return link


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]
