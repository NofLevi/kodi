"""Real-Debrid.

Two verified facts shape this client:

* The API allows 250 requests a minute, refused requests still count, and
  hammering it earns an indefinite block. So cache answers are remembered and
  probing is done for one candidate at a time, never for a whole result list.
* instantAvailability is gone from the documentation. Cached status therefore
  comes from whatever the aggregator was told by the indexer, and is confirmed
  only for the source actually being played, by adding it and reading back its
  status.

Sign-in is the device flow for open source apps: ask for a code, the user types
it at real-debrid.com/device, then poll until a per-user client id and secret
come back and exchange those for a token.
"""
import time

from .. import http, kodi, settings
from . import base

API = "https://api.real-debrid.com/rest/1.0"
OAUTH = "https://api.real-debrid.com/oauth/v2"
OPEN_SOURCE_CLIENT_ID = "X245A4XAIBGVM"
DEVICE_URL = "https://real-debrid.com/device"


class RealDebrid(base.DebridService):
    name = "realdebrid"
    label = "Real-Debrid"

    def configured(self):
        return bool(settings.get("realdebrid.token"))

    def _headers(self):
        return {"Authorization": "Bearer %s" % self._token()}

    def _token(self):
        token = settings.get("realdebrid.token")
        expires = settings.get_int("realdebrid.expires")
        if token and expires and expires - time.time() < 600:
            refreshed = self._refresh()
            if refreshed:
                return refreshed
        return token

    # -- authorisation -----------------------------------------------------

    # This used to say Real-Debrid had no typed key, on the grounds that the
    # device flow mints a per-user client id and secret and refreshing needs
    # both. That is true of a *minted* token and not of the private one on
    # real-debrid.com/apitoken, which does not expire: `token()` only tries to
    # refresh when an expiry is stored, so a private token with none is simply
    # used as it is. The effect of the old reading was that pressing
    # Real-Debrid dropped you into a QR code with no way to see anything else.
    methods = ("scan", "key")
    key_url = "https://real-debrid.com/apitoken"
    key_setting = "realdebrid.token"

    def credential_settings(self):
        # All five, because the device flow mints a client id and secret of
        # its own and a refresh needs both. Clearing only the token would
        # leave a half-signed-out account that looks disconnected and still
        # holds credentials.
        return ["realdebrid.token", "realdebrid.refresh",
                "realdebrid.expires", "realdebrid.client_id",
                "realdebrid.client_secret"]

    def authorize(self, method=None):
        if method == "key":
            return self.authorize_with_key(None)
        return self._device_flow()

    def authorize_with_key(self, key=None):
        """A private API token, typed or pasted, rather than a minted one.

        The OAuth settings are cleared alongside: leaving a stale client id,
        secret or expiry behind would have `token()` try to refresh a token
        that was never minted, and fail in a way that looks like the account
        going bad rather than like this.
        """
        from ..ui import signin

        previous = {name: settings.get(name)
                    for name in self.credential_settings()}
        if key is None:
            key = signin.ask_for_key("%s API token" % self.label,
                                     settings.get("realdebrid.token"),
                                     help_url=self.key_url)
            if key is None:
                return False

        settings.set("realdebrid.token", key.strip())
        for name in ("realdebrid.refresh", "realdebrid.client_id",
                     "realdebrid.client_secret"):
            settings.set(name, "")
        settings.set("realdebrid.expires", "0")

        if self.account_info():
            return True
        # Put back whatever was working before rather than leaving the viewer
        # signed out because they mistyped a replacement token.
        for name, value in previous.items():
            settings.set(name, value)
        return False

    def _device_flow(self):
        from ..ui import signin

        payload = http.get_json("%s/device/code" % OAUTH,
                                params={"client_id": OPEN_SOURCE_CLIENT_ID,
                                        "new_credentials": "yes"},
                                timeout=base.timeout_for("auth"), default=None)
        if not payload:
            return False

        device_code = payload.get("device_code")
        found = {}

        def poll():
            answer = http.get_json("%s/device/credentials" % OAUTH,
                                   params={"client_id": OPEN_SOURCE_CLIENT_ID,
                                           "code": device_code},
                                   default=None)
            if answer and answer.get("client_id"):
                found.update(answer)
                return True
            return False

        # `direct_verification_url` carries the device id, so scanning it
        # authorises this box without the eight-character code being typed
        # anywhere at all. Real-Debrid returns both and we were using the
        # plain one, which is the same page with the work left to do. The
        # code still goes on screen beside it, because somebody reading the
        # short link off the television needs it.
        if not signin.run_device(
                self.label,
                (payload.get("direct_verification_url")
                 or payload.get("verification_url") or DEVICE_URL),
                payload.get("user_code", ""),
                poll,
                lifetime=payload.get("expires_in"),
                interval=max(5, int(payload.get("interval") or 5)),
        ):
            return False
        return self._exchange(found, device_code)

    def _exchange(self, credentials, device_code):
        response = http.post("%s/token" % OAUTH, data={
            "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"],
            "code": device_code,
            "grant_type": "http://oauth.net/grant_type/device/1.0",
        }, timeout=base.timeout_for("auth"))
        if response is None or response.status_code != 200:
            return False
        token = response.json()
        settings.set_many({
            "realdebrid.client_id": credentials["client_id"],
            "realdebrid.client_secret": credentials["client_secret"],
            "realdebrid.token": token.get("access_token", ""),
            "realdebrid.refresh": token.get("refresh_token", ""),
            "realdebrid.expires": str(int(time.time() +
                                          int(token.get("expires_in") or 0))),
        })
        return bool(token.get("access_token"))

    def _refresh(self):
        refresh = settings.get("realdebrid.refresh")
        client_id = settings.get("realdebrid.client_id")
        secret = settings.get("realdebrid.client_secret")
        if not (refresh and client_id and secret):
            return ""
        response = http.post("%s/token" % OAUTH, data={
            "client_id": client_id,
            "client_secret": secret,
            "code": refresh,
            "grant_type": "http://oauth.net/grant_type/device/1.0",
        }, timeout=base.timeout_for("auth"))
        if response is None or response.status_code != 200:
            kodi.log("Real-Debrid token refresh failed")
            return ""
        token = response.json()
        settings.set_many({
            "realdebrid.token": token.get("access_token", ""),
            "realdebrid.refresh": token.get("refresh_token", refresh),
            "realdebrid.expires": str(int(time.time() +
                                          int(token.get("expires_in") or 0))),
        })
        return token.get("access_token", "")

    def account_info(self):
        if not self.configured():
            return None
        payload = http.get_json("%s/user" % API, headers=self._headers(),
                                timeout=base.timeout_for("auth"), default=None)
        if not payload:
            return None
        return {
            "user": payload.get("username", ""),
            "plan": payload.get("type", ""),
            "expires": payload.get("expiration", ""),
            "is_free": payload.get("type") != "premium",
        }

    def is_cached(self, hashes):
        """Real-Debrid no longer exposes a bulk cache check.

        Returning nothing is honest: the aggregator then trusts the indexer
        flags rather than inventing an answer, and the real check happens once,
        for the single source the user actually plays.
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

        added = http.post_json("%s/torrents/addMagnet" % API,
                               headers=self._headers(),
                               data={"magnet": magnet},
                               timeout=base.timeout_for("resolve"), default=None)
        torrent_id = (added or {}).get("id")
        if not torrent_id:
            return ""

        try:
            info = self._info(torrent_id)
            if not info:
                return ""
            files = [{"id": entry.get("id"), "name": entry.get("path", ""),
                      "size": entry.get("bytes", 0)}
                     for entry in info.get("files") or []]
            chosen = self.pick_file(files, source,
                                    source.get("extra", {}).get("meta"))
            if not chosen:
                self._delete(torrent_id)
                return ""

            http.post("%s/torrents/selectFiles/%s" % (API, torrent_id),
                      headers=self._headers(),
                      data={"files": str(chosen["id"])},
                      timeout=base.timeout_for("resolve"))

            info = self._wait_ready(torrent_id, source)
            if not info:
                return ""
            links = info.get("links") or []
            if not links:
                return ""
            source["file_name"] = chosen["name"]
            return self._unrestrict(links[0])
        except Exception:
            kodi.log_exception("Real-Debrid resolve failed")
            self._delete(torrent_id)
            return ""

    def _info(self, torrent_id):
        return http.get_json("%s/torrents/info/%s" % (API, torrent_id),
                             headers=self._headers(),
                             timeout=base.timeout_for("resolve"), default=None)

    def _wait_ready(self, torrent_id, source, attempts=6):
        """A cached torrent is ready almost at once; an uncached one is not.

        Rather than sit through a download, give up quickly and remove the
        torrent, so the account is not left full of abandoned transfers.
        """
        allow_uncached = source.get("extra", {}).get("allow_uncached", False)
        for attempt in range(attempts):
            info = self._info(torrent_id)
            if not info:
                return None
            status = info.get("status")
            if status == "downloaded":
                return info
            if status in ("magnet_error", "error", "virus", "dead"):
                self._delete(torrent_id)
                return None
            if not allow_uncached and attempt >= 2:
                kodi.log("Real-Debrid source is not cached, dropping it")
                self._delete(torrent_id)
                return None
            time.sleep(1.5)
        if not allow_uncached:
            self._delete(torrent_id)
        return None

    def _unrestrict(self, link):
        payload = http.post_json("%s/unrestrict/link" % API,
                                 headers=self._headers(), data={"link": link},
                                 timeout=base.timeout_for("resolve"), default=None)
        return (payload or {}).get("download", "")

    def _delete(self, torrent_id):
        try:
            http.request("DELETE", "%s/torrents/delete/%s" % (API, torrent_id),
                         headers=self._headers(), retries=0)
        except Exception:
            pass
