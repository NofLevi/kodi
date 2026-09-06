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
