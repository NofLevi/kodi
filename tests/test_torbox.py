"""TorBox, against the shapes its API really returns.

Until tonight this module had never executed. The fixtures below are the
actual responses observed from api.torbox.app on 2026-09-07, not invented
ones, because two of them were genuinely unknown and either would have been a
silent total failure:

* `checkcached` answers with a list of objects carrying a `hash` field, and
  simply omits a hash it does not have. Had it been read as a list of plain
  strings, every source would have looked uncached, and with `cached_only` on
  by default nothing would ever have played.
* `requestdl` answers with a bare URL string, not an object.
"""
import pytest

from katan import http, settings
from katan.debrid import registry, torbox

KEY = "not-a-real-key-0000-0000-000000000000"
BIG_BUCK = "dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c"
SINTEL = "08ada5a7a6183aae1e09d831df6748d566095a10"
MISSING = "0000000000000000000000000000000000000000"

# Exactly the body api.torbox.app returned for a three-hash query.
CHECKCACHED = {
    "success": True,
    "error": None,
    "detail": "Torrent cache status retrieved successfully.",
    "data": [
        {"name": "Sintel", "size": 129302391, "hash": SINTEL},
        {"name": "Big Buck Bunny", "size": 276445467, "hash": BIG_BUCK},
    ],
}

TORRENT = {
    "id": 4242,
    "hash": BIG_BUCK,
    "cached": True,
    "download_finished": True,
    "files": [
        {"id": 0, "name": "Big Buck Bunny/Big Buck Bunny.mp4",
         "size": 276445467},
        {"id": 1, "name": "Big Buck Bunny/sample.mp4", "size": 4000000},
        {"id": 2, "name": "Big Buck Bunny/poster.jpg", "size": 120000},
    ],
}


@pytest.fixture
def configured(settings_module):
    settings_module.set("torbox.apikey", KEY)
    return settings_module


@pytest.fixture
def api(monkeypatch, configured):
    """Serve the real response shapes and record every call."""
    calls = []
    state = {"checkcached": CHECKCACHED,
             "mylist": {"success": True, "data": [TORRENT]},
             "requestdl": {"success": True, "data": "https://cdn/file.mp4?token=x"}}

    def fake_get_json(url, default=None, **kwargs):
        calls.append({"url": url, "params": kwargs.get("params") or {},
                      "headers": kwargs.get("headers") or {}})
        for name, payload in state.items():
            if name in url:
                return payload
        return default

    monkeypatch.setattr(http, "get_json", fake_get_json)
    return {"calls": calls, "state": state}


def client():
    return registry.get("torbox")


# --------------------------------------------------------------------------
# the shapes that were unknown
# --------------------------------------------------------------------------


def test_a_list_of_objects_is_understood(api):
    """The real shape. Reading it wrong means nothing ever plays."""
    answer = client().is_cached([BIG_BUCK, SINTEL, MISSING])
    assert answer[BIG_BUCK] is True
    assert answer[SINTEL] is True


def test_an_omitted_hash_is_a_definite_no(api):
    """TorBox leaves out what it does not have; absence has to mean False."""
    answer = client().is_cached([BIG_BUCK, MISSING])
    assert answer[MISSING] is False, "a miss must be False, not missing"


def test_a_list_of_plain_strings_still_works(api):
    """An older documented shape, kept working so a change does not break us."""
    api["state"]["checkcached"] = {"data": [BIG_BUCK.upper()]}
    assert client().is_cached([BIG_BUCK])[BIG_BUCK] is True


def test_an_object_keyed_by_hash_still_works(api):
    api["state"]["checkcached"] = {"data": {BIG_BUCK: {"name": "x"}}}
    assert client().is_cached([BIG_BUCK])[BIG_BUCK] is True


def test_a_bare_url_string_is_the_link(api):
    """requestdl really answers with a string, not an object."""
    source = {"hash": BIG_BUCK, "title": "Big Buck Bunny 1080p",
              "extra": {"meta": {"type": "movie"}}}
    assert client().resolve(source) == "https://cdn/file.mp4?token=x"


def test_an_object_link_is_also_accepted(api):
    api["state"]["requestdl"] = {"data": {"url": "https://cdn/other.mp4"}}
    source = {"hash": BIG_BUCK, "title": "x", "extra": {"meta": {"type": "movie"}}}
    assert client().resolve(source) == "https://cdn/other.mp4"


# --------------------------------------------------------------------------
# how the key travels
# --------------------------------------------------------------------------


def test_most_calls_use_a_bearer_header(api):
    client().is_cached([BIG_BUCK])
    assert api["calls"][0]["headers"].get("Authorization") == "Bearer %s" % KEY


def test_the_download_call_uses_a_token_parameter_instead(api):
    """requestdl is the one endpoint that wants the key in the query."""
    source = {"hash": BIG_BUCK, "title": "x", "extra": {"meta": {"type": "movie"}}}
    client().resolve(source)
    call = [c for c in api["calls"] if "requestdl" in c["url"]][0]
    assert call["params"].get("token") == KEY
    assert "Authorization" not in (call["headers"] or {})


def test_nothing_happens_without_a_key(settings_module, no_network):
    settings_module.set("torbox.apikey", "")
    assert client().configured() is False
    assert client().is_cached([BIG_BUCK]) == {}
    assert client().resolve({"hash": BIG_BUCK}) == ""


# --------------------------------------------------------------------------
# choosing the file, and staying cheap
# --------------------------------------------------------------------------


def test_the_feature_file_is_chosen_not_the_sample(api):
    source = {"hash": BIG_BUCK, "title": "Big Buck Bunny 1080p",
              "extra": {"meta": {"type": "movie"}}}
    client().resolve(source)
    assert source["file_name"] == "Big Buck Bunny/Big Buck Bunny.mp4"


def test_a_torrent_already_on_the_account_is_reused(api):
    """Adding it again would spend one of the 60 uncached adds an hour."""
    source = {"hash": BIG_BUCK, "title": "x", "extra": {"meta": {"type": "movie"}}}
    client().resolve(source)
    assert not [c for c in api["calls"] if "createtorrent" in c["url"]], \
        "it was already on the account; it must not be added again"


def test_hashes_are_batched(api):
    """The check is a GET, so the URL length caps how many hashes fit."""
    assert torbox.CHECK_BATCH <= 100, "the docs put the URL limit near 100"

    wanted = 200
    client().is_cached(["%040d" % n for n in range(wanted)])
    checks = [c for c in api["calls"] if "checkcached" in c["url"]]

    expected = -(-wanted // torbox.CHECK_BATCH)     # ceiling division
    assert len(checks) == expected, \
        "%d hashes at %d per batch should be %d calls, not %d" % (
            wanted, torbox.CHECK_BATCH, expected, len(checks))
    assert all(len(c["params"]["hash"].split(",")) <= torbox.CHECK_BATCH
               for c in checks), "a batch exceeded the cap"


def test_a_service_that_does_not_answer_is_not_a_crash(monkeypatch, configured):
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw: None)
    assert client().is_cached([BIG_BUCK]) == {}
    assert client().resolve({"hash": BIG_BUCK, "title": "x",
                             "extra": {"meta": {}}}) == ""


# --------------------------------------------------------------------------
# the account
# --------------------------------------------------------------------------


def test_the_plan_is_named_not_numbered(monkeypatch, configured):
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw: {
        "data": {"plan": 1, "email": "someone@example.com",
                 "premium_expires_at": "2026-10-23T13:54:48Z"}})
    info = client().account_info()
    assert info["plan"] == "Essential"
    assert info["is_free"] is False


def test_a_free_account_is_flagged(monkeypatch, configured):
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw: {
        "data": {"plan": 0, "email": "x@y.z"}})
    assert client().account_info()["is_free"] is True


def test_torbox_is_registered_and_gated_on_its_key(configured):
    assert "torbox" in settings.configured_debrid()
    settings.set("torbox.apikey", "")
    assert "torbox" not in settings.configured_debrid()


# --------------------------------------------------------------------------
# which call goes first, and why it is worth caring
# --------------------------------------------------------------------------


class FakeResponse(object):
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._payload


@pytest.fixture
def creating(monkeypatch, api):
    """Answer createtorrent the way TorBox answers an existing torrent."""
    posts = []

    def fake_post(url, **kwargs):
        posts.append({"url": url, "data": kwargs.get("data") or {}})
        return FakeResponse({
            "success": True, "error": None,
            "detail": "Found Cached Torrent. Using Cached Torrent.",
            "data": {"hash": BIG_BUCK, "torrent_id": TORRENT["id"]}})

    monkeypatch.setattr(http, "post", fake_post)
    api["posts"] = posts
    return api


def source_for(**extra):
    entry = {"hash": BIG_BUCK, "title": "Big Buck Bunny 1080p",
             "magnet": "magnet:?xt=urn:btih:%s" % BIG_BUCK,
             "extra": {"meta": {"type": "movie"}}}
    entry["extra"].update(extra)
    return entry


def whole_account(calls):
    """Calls that pulled the entire account rather than one torrent.

    Both go to /torrents/mylist and they differ only by an `id` parameter,
    which is easy to miss: with an id it is one torrent, without one it is
    every torrent you have ever added.
    """
    return [c for c in calls
            if "mylist" in c["url"] and not c["params"].get("id")]


def test_the_account_list_is_not_downloaded_to_play_a_cached_source(creating):
    """mylist without an id is 466 KB and two to four seconds against a real
    account, and it gets slower every time anything is played. createtorrent
    answers "Found Cached Torrent" with the same torrent id in under half a
    second, so it is asked first."""
    assert client().resolve(source_for()) == "https://cdn/file.mp4?token=x"

    assert not whole_account(creating["calls"]), \
        "the whole account list should not have been downloaded"
    assert creating["posts"], "createtorrent should have been called"


def test_adding_is_refused_for_an_uncached_torrent_by_default(creating):
    """add_only_if_cached is what protects the sixty-an-hour quota."""
    client().resolve(source_for())
    assert creating["posts"][0]["data"]["add_only_if_cached"] == "true"


def test_the_account_list_is_the_fallback_when_adding_will_not_do(api,
                                                                 monkeypatch):
    """A torrent that finished downloading but is no longer cached is one
    add_only_if_cached refuses and the account list still finds."""
    monkeypatch.setattr(http, "post",
                        lambda url, **kwargs: FakeResponse(
                            {"success": False, "detail": "not cached"}))
    assert client().resolve(source_for()) == "https://cdn/file.mp4?token=x"
    assert any("mylist" in c["url"] for c in api["calls"]), \
        "it should have fallen back to the account list"


def test_looking_before_leaping_when_a_download_may_be_started(creating):
    """With uncached adds allowed, adding is not free - it spends one of sixty
    an hour - so the account list is checked first after all."""
    client().resolve(source_for(allow_uncached=True))
    assert any("mylist" in c["url"] for c in creating["calls"]), \
        "the account list should have been consulted first"


def test_a_torrent_torbox_is_still_fetching_is_not_played(api, monkeypatch):
    """An indexer marks a source cached, TorBox accepts the magnet, and it can
    still turn out that TorBox does not have it: state "downloading", cached
    false, no file list. There is nothing to pick from, and the old message -
    "no usable video file" - said the wrong thing about why."""
    downloading = {"id": 99, "hash": BIG_BUCK, "cached": False,
                   "download_finished": False,
                   "download_state": "downloading", "files": []}
    api["state"]["mylist"] = {"success": True, "data": [downloading]}
    monkeypatch.setattr(http, "post",
                        lambda url, **kwargs: FakeResponse(
                            {"success": True,
                             "data": {"torrent_id": 99, "hash": BIG_BUCK}}))

    assert client().resolve(source_for()) == ""


def test_waiting_only_happens_when_torbox_says_it_has_the_torrent(api,
                                                                 monkeypatch):
    """The viewer is in front of a black screen, so a dead end must be a fast
    dead end rather than one with three seconds of hope in it."""
    slept = []
    monkeypatch.setattr(torbox.time, "sleep", lambda seconds: slept.append(seconds))
    api["state"]["mylist"] = {"success": True, "data": [
        {"id": 99, "hash": BIG_BUCK, "cached": False,
         "download_finished": False, "download_state": "downloading",
         "files": []}]}
    monkeypatch.setattr(http, "post",
                        lambda url, **kwargs: FakeResponse(
                            {"success": True,
                             "data": {"torrent_id": 99, "hash": BIG_BUCK}}))

    client().resolve(source_for())
    assert not slept, "there is no point waiting for a torrent still downloading"


def test_a_file_list_that_has_not_arrived_yet_is_waited_for(api, monkeypatch):
    """A torrent TorBox says it has, whose files are a moment behind."""
    slept = []
    monkeypatch.setattr(torbox.time, "sleep", lambda seconds: slept.append(seconds))
    answers = [
        {"id": 99, "hash": BIG_BUCK, "cached": True, "download_finished": True,
         "download_state": "cached", "files": []},
        dict(TORRENT, id=99),
    ]

    def fake_get_json(url, default=None, **kwargs):
        if "mylist" in url:
            return {"success": True, "data": [answers.pop(0) if answers
                                              else dict(TORRENT, id=99)]}
        if "requestdl" in url:
            return {"success": True, "data": "https://cdn/file.mp4?token=x"}
        return default

    monkeypatch.setattr(http, "get_json", fake_get_json)
    monkeypatch.setattr(http, "post",
                        lambda url, **kwargs: FakeResponse(
                            {"success": True,
                             "data": {"torrent_id": 99, "hash": BIG_BUCK}}))

    assert client().resolve(source_for()) == "https://cdn/file.mp4?token=x"
    assert slept, "it should have waited for the file list"


# --------------------------------------------------------------------------
# the device flow
#
# TorBox added one for TV apps, and it is the reason this service no longer
# asks anybody to type thirty-two characters on a remote. These fixtures are
# the bodies api.torbox.app returned on 2026-09-08, because its own OpenAPI
# document publishes no schema at all for either success response.
# --------------------------------------------------------------------------

DEVICE_START = {
    "success": True,
    "error": None,
    "detail": "Successfully created your device code authorization.",
    "data": {
        "device_code": "44134df0d789de14ddaf1dbc1c1f3a7e",
        "interval": 5,
        "expires_at": "2026-09-08T11:10:06Z",
        "verification_url": "https://torbox.app/oauth/device?app=Katan",
        "friendly_verification_url": "https://tor.box/link",
        "code": "540608",
    },
}

# Both of these are HTTP 400. Only the error name says which is which, and
# getting that wrong either freezes the screen for ten minutes on a dead code
# or throws the viewer out while they are still reaching for their phone.
WAITING = {"success": False, "error": "DEVICE_CODE_NOT_USED",
           "detail": "This device code has not been used yet. Please wait for "
                     "the user to scan the code.", "data": None}
DEAD = {"success": False, "error": "ITEM_NOT_FOUND",
        "detail": "The device code you entered is invalid or has expired. "
                  "Please try again.", "data": None}


class _Response(object):
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _device(monkeypatch, answers, start=DEVICE_START):
    """Run the flow, feeding `answers` to the poll one at a time."""
    monkeypatch.setattr(torbox.http, "get_json",
                        lambda url, **kw: start if "device/start" in url else None)
    monkeypatch.setattr(torbox.http, "post",
                        lambda url, **kw: answers.pop(0))

    seen = {}

    def run_device(title, url, code, poll, lifetime=None, interval=5):
        seen.update(url=url, code=code, lifetime=lifetime, interval=interval)
        seen["answers"] = []
        while answers:
            answer = poll()
            seen["answers"].append(answer)
            if answer is not True and answer is not False:
                return False
            if answer:
                return True
        return False

    from katan.ui import signin
    monkeypatch.setattr(signin, "run_device", run_device)
    return seen


def test_the_device_code_and_its_page_reach_the_screen(monkeypatch, settings_module):
    seen = _device(monkeypatch, [_Response(400, WAITING)])
    torbox.TorBox().authorize("scan")

    assert seen["code"] == "540608"
    assert seen["url"] == "https://torbox.app/oauth/device?app=Katan"
    assert seen["interval"] == 5


def test_waiting_and_dead_are_told_apart(monkeypatch, settings_module):
    """Both are HTTP 400. Confusing them is a screen that never gives up."""
    seen = _device(monkeypatch, [_Response(400, WAITING), _Response(400, DEAD)])
    assert torbox.TorBox().authorize("scan") is False
    assert seen["answers"] == [False, None]


@pytest.mark.parametrize("data", [
    "a-real-looking-torbox-key-000000",
    {"token": "a-real-looking-torbox-key-000000"},
    {"api_key": "a-real-looking-torbox-key-000000"},
])
def test_the_approved_token_is_kept_however_it_arrives(data, monkeypatch,
                                                       settings_module):
    """The 200 has no published schema, and this is the service whose
    requestdl answers with a bare string where everything else answers with
    an object. So all of these have to work."""
    approved = _Response(200, {"success": True, "data": data})
    _device(monkeypatch, [approved])
    monkeypatch.setattr(torbox.TorBox, "account_info",
                        lambda self: {"user": "someone", "plan": "Pro"})

    assert torbox.TorBox().authorize("scan") is True
    assert settings.get("torbox.apikey") == "a-real-looking-torbox-key-000000"


def test_a_token_the_account_call_refuses_is_not_kept(monkeypatch,
                                                      settings_module):
    """And it ends the attempt rather than leaving the screen waiting."""
    settings.set("torbox.apikey", "the-one-that-works")
    seen = _device(monkeypatch, [_Response(200, {"data": "a-dud"})])
    monkeypatch.setattr(torbox.TorBox, "account_info", lambda self: None)

    assert torbox.TorBox().authorize("scan") is False
    assert seen["answers"] == [None]
    assert settings.get("torbox.apikey") == "the-one-that-works"


def test_the_expiry_becomes_a_countdown(monkeypatch):
    """expires_at is a moment; the sign-in screen wants a number of seconds."""
    import time as _time
    monkeypatch.setattr(torbox.time, "time",
                        lambda: _time.mktime((2026, 9, 8, 11, 5, 6, 0, 0, 0))
                        - _time.timezone)

    assert torbox._seconds_until("2026-09-08T11:10:06Z") == 300
    # A clock that disagrees with TorBox must not close the screen at once,
    # nor leave one up all evening.
    assert torbox._seconds_until("2020-01-01T00:00:00Z") == 60
    assert torbox._seconds_until("2099-01-01T00:00:00Z") == 1800
    assert torbox._seconds_until("") == 600
    assert torbox._seconds_until("not a timestamp") == 600
