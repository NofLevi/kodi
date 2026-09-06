"""SeaDex: the one case where release group matters more than the numbers.

Two 1080p anime encodes can differ by a botched encode or the wrong audio
track, and the release name does not say which is which. These tests pin down
that a SeaDex pick wins on merit, that matching is by infohash so the wrong
file can never be promoted, and that a title SeaDex has never heard of costs
nothing.
"""
import pytest

from katan import cache, http, settings
from katan.meta import seadex
from katan.sources import model, scoring

GOOD = "a" * 40
ALSO_GOOD = "b" * 40
OTHER = "f" * 40


def entry(*releases):
    return {"items": [{"alID": 16498,
                       "expand": {"trs": list(releases)}}]}


def release(digest, best=True, group="GRP"):
    return {"infoHash": digest, "isBest": best, "releaseGroup": group,
            "tracker": "Nyaa"}


@pytest.fixture
def api(monkeypatch):
    calls = []
    state = {"payload": entry(release(GOOD), release(ALSO_GOOD),
                              release(OTHER, best=False))}

    def fake_get_json(url, default=None, **kwargs):
        calls.append(kwargs.get("params") or {})
        return state["payload"]

    monkeypatch.setattr(http, "get_json", fake_get_json)
    return {"calls": calls, "state": state}


def source(title, digest, seeders=10):
    return model.from_release_name(title, "nyaa", size=2 * 1024 ** 3,
                                   seeders=seeders, info_hash=digest)


# --------------------------------------------------------------------------
# the lookup
# --------------------------------------------------------------------------


def test_only_the_best_releases_are_returned(api):
    found = seadex.best_hashes(16498)
    assert found == {GOOD, ALSO_GOOD}, "a release not marked best is not a pick"


def test_the_title_is_looked_up_by_anilist_id(api):
    seadex.best_hashes(16498)
    assert api["calls"][0]["filter"] == "(alID=16498)"
    assert api["calls"][0]["expand"] == "trs"


def test_the_answer_is_cached(api):
    seadex.best_hashes(16498)
    seadex.best_hashes(16498)
    assert len(api["calls"]) == 1


def test_a_title_with_no_entry_is_cached_too(api):
    """Otherwise every search for an uncovered anime pays for a request."""
    api["state"]["payload"] = {"items": []}
    assert seadex.best_hashes(16498) == set()
    assert seadex.best_hashes(16498) == set()
    assert len(api["calls"]) == 1


def test_a_malformed_hash_is_ignored(api):
    api["state"]["payload"] = entry(release("not-a-hash"), release("1234"),
                                    release(GOOD))
    assert seadex.best_hashes(16498) == {GOOD}


def test_no_anilist_id_means_no_request(api):
    assert seadex.best_hashes(None) == set()
    assert seadex.best_hashes(0) == set()
    assert not api["calls"]


def test_the_setting_switches_it_off(api, settings_module):
    settings_module.set("sources.seadex", "false")
    assert seadex.best_hashes(16498) == set()
    assert not api["calls"]


def test_a_service_that_does_not_answer_is_no_opinion(monkeypatch):
    monkeypatch.setattr(http, "get_json", lambda url, default=None, **kw: None)
    assert seadex.best_hashes(16498) == set()


def test_a_nonsense_answer_is_no_opinion(monkeypatch):
    monkeypatch.setattr(http, "get_json",
                        lambda url, default=None, **kw: {"items": "nope"})
    assert seadex.best_hashes(16498) == set()


# --------------------------------------------------------------------------
# what it does to the ranking
# --------------------------------------------------------------------------


def test_a_seadex_pick_beats_a_far_more_seeded_release(api):
    picked = source("Show S01E01 1080p BluRay x264-GOOD", GOOD, seeders=20)
    popular = source("Show S01E01 1080p BluRay x264-OTHER", OTHER, seeders=900)

    kept, _rejected = scoring.rank([popular, picked],
                                   meta={"ids": {"anilist": 16498}})
    assert kept[0]["hash"] == GOOD
    assert kept[0]["seadex"] is True


def test_a_cached_source_still_beats_a_seadex_pick(api, settings_module):
    """Waiting for a download is worse than a slightly worse encode."""
    settings_module.set("sources.cached_only", "false")
    picked = source("Show S01E01 1080p BluRay x264-GOOD", GOOD)
    cached = source("Show S01E01 1080p BluRay x264-OTHER", OTHER)
    cached["cached"] = True

    kept, _rejected = scoring.rank([picked, cached],
                                   meta={"ids": {"anilist": 16498}})
    assert kept[0]["hash"] == OTHER
    assert scoring.WEIGHT_SEADEX < scoring.WEIGHT_CACHED


def test_matching_is_by_hash_not_by_name(api):
    """A release that merely looks like the pick must not be promoted."""
    impostor = source("Show S01E01 1080p BluRay x264-GRP", OTHER)
    kept, _rejected = scoring.rank([impostor],
                                   meta={"ids": {"anilist": 16498}})
    assert not kept[0].get("seadex")


def test_a_film_never_consults_seadex(api):
    picked = source("A Film 2019 1080p BluRay x264-GRP", GOOD)
    kept, _rejected = scoring.rank([picked], meta={"ids": {"tmdb": 550}})
    assert kept, "the film should still rank"
    assert not api["calls"], "a film has no anilist id and must cost nothing"


def test_ranking_survives_seadex_failing(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("seadex exploded")

    monkeypatch.setattr(seadex, "best_hashes", boom)
    picked = source("Show S01E01 1080p BluRay x264-GRP", GOOD)
    kept, _rejected = scoring.rank([picked], meta={"ids": {"anilist": 16498}})
    assert kept, "a broken SeaDex must not break the picker"


def test_the_setting_has_a_default(settings_module):
    assert settings.DEFAULTS.get("sources.seadex") == "true"
