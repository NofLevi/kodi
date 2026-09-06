"""File picking is where a season pack becomes the right episode."""
import pytest

from katan.debrid import base


@pytest.fixture
def service():
    return base.DebridService()


def files(*entries):
    return [{"name": name, "size": size} for name, size in entries]


def test_single_video_file_is_chosen(service):
    chosen = service.pick_file(files(("Movie.2024.1080p.mkv", 5 * 1024 ** 3)), {})
    assert chosen["name"].endswith(".mkv")


def test_samples_and_extras_are_ignored(service):
    chosen = service.pick_file(files(
        ("Movie.2024.1080p-sample.mkv", 90 * 1024 ** 2),
        ("Movie.2024.1080p.mkv", 6 * 1024 ** 3),
        ("Featurette.mkv", 700 * 1024 ** 2),
    ), {})
    assert chosen["name"] == "Movie.2024.1080p.mkv"


def test_non_video_files_are_ignored(service):
    chosen = service.pick_file(files(
        ("readme.txt", 1024),
        ("cover.jpg", 500 * 1024),
        ("Movie.2024.1080p.mkv", 6 * 1024 ** 3),
    ), {})
    assert chosen["name"].endswith(".mkv")


def test_tiny_files_are_ignored(service):
    """Anything under the minimum is a trailer or a decoy, never the feature."""
    assert service.pick_file(files(("Movie.mkv", 5 * 1024 ** 2)), {}) is None


def test_the_right_episode_is_picked_from_a_season_pack(service):
    pack = files(
        ("Show/Show.S02E01.1080p.WEB-DL.mkv", 2 * 1024 ** 3),
        ("Show/Show.S02E02.1080p.WEB-DL.mkv", 2 * 1024 ** 3),
        ("Show/Show.S02E03.1080p.WEB-DL.mkv", 2 * 1024 ** 3),
    )
    chosen = service.pick_file(pack, {}, {"type": "episode", "season": 2,
                                          "episode": 2})
    assert "S02E02" in chosen["name"]


def test_a_pack_without_the_wanted_episode_returns_nothing(service):
    """Playing the wrong episode is worse than failing to play at all."""
    pack = files(
        ("Show.S02E01.1080p.mkv", 2 * 1024 ** 3),
        ("Show.S02E02.1080p.mkv", 2 * 1024 ** 3),
    )
    assert service.pick_file(pack, {}, {"type": "episode", "season": 2,
                                        "episode": 9}) is None


def test_absolute_numbering_works_for_anime_packs(service):
    pack = files(
        ("[Group] Show - 11 [1080p].mkv", 1400 * 1024 ** 2),
        ("[Group] Show - 12 [1080p].mkv", 1400 * 1024 ** 2),
    )
    chosen = service.pick_file(pack, {}, {"type": "episode", "season": 1,
                                          "episode": 12})
    assert "- 12 " in chosen["name"]


def test_largest_video_wins_for_a_movie(service):
    chosen = service.pick_file(files(
        ("Movie.2024.1080p.mkv", 6 * 1024 ** 3),
        ("Movie.2024.720p.mkv", 2 * 1024 ** 3),
    ), {})
    assert "1080p" in chosen["name"]


def test_empty_input_is_safe(service):
    assert service.pick_file([], {}) is None
    assert service.pick_file(None, {}) is None


def test_cache_answers_are_remembered(monkeypatch):
    """A repeated cache question must not become a repeated API call."""
    calls = []

    class Fake(base.DebridService):
        name = "fake"

        def is_cached(self, hashes):
            calls.append(list(hashes))
            return {h: h.startswith("a") for h in hashes}

    service = Fake()
    hashes = ["a" * 40, "b" * 40]

    first = service.cached_with_memory(hashes)
    second = service.cached_with_memory(hashes)

    assert first == second == {"a" * 40: True, "b" * 40: False}
    assert len(calls) == 1, "the second lookup should come from the cache"


def test_a_failing_service_does_not_break_the_caller():
    class Broken(base.DebridService):
        name = "broken"

        def is_cached(self, hashes):
            raise RuntimeError("service is down")

    assert Broken().cached_with_memory(["a" * 40]) == {}
