"""Kids mode, which replaces the home screen rather than filtering it.

The design claim these tests exist to defend: filtering alone cannot make a
safe home screen, because a TMDB list result carries no certification, so a
filter that waits to see one lets everything through. Kids mode therefore has
to swap the row set, and both halves have to hold.
"""
import pytest

from katan import catalog, kids, settings


@pytest.fixture
def kids_on(settings_module):
    settings_module.set("kids.enabled", "true")
    settings_module.set("kids.age", "older")
    return settings_module


def item(title, genres=(), mpaa="", adult=False):
    from katan.meta import items
    return items.new_item("movie", title=title, genres=list(genres),
                          mpaa=mpaa, extra={"adult": adult})


# --------------------------------------------------------------------------
# the row swap, which is the actual protection
# --------------------------------------------------------------------------


def test_kids_mode_replaces_every_row(kids_on):
    assert catalog.enabled_row_ids() == kids.ROW_IDS


def test_the_normal_rows_come_back_when_it_is_off(settings_module):
    settings_module.set("kids.enabled", "false")
    assert catalog.enabled_row_ids() != kids.ROW_IDS
    assert "trending_movies" in catalog.enabled_row_ids()


def test_a_configured_row_order_is_ignored_in_kids_mode(kids_on, settings_module):
    """A child must not inherit whatever the adult had pinned to the top."""
    settings_module.set("ui.rows", "trending_movies,box_office")
    assert catalog.enabled_row_ids() == kids.ROW_IDS


def test_every_kids_row_exists(kids_on):
    for row_id in kids.ROW_IDS:
        assert catalog.by_id(row_id) is not None, row_id


def test_no_kids_row_is_on_by_default():
    """They are reached by turning the mode on, not by appearing for everyone."""
    for row_id in kids.ROW_IDS:
        assert catalog.by_id(row_id)["default"] is False


# --------------------------------------------------------------------------
# the filter, as the second line
# --------------------------------------------------------------------------


def test_a_blocked_genre_is_refused(kids_on):
    assert not kids.allows(item("A Nasty Film", genres=["Horror"]))
    assert not kids.allows(item("A War Film", genres=["War", "Drama"]))


def test_a_family_genre_is_allowed(kids_on):
    assert kids.allows(item("A Nice Film", genres=["Family", "Animation"]))


def test_an_adult_flag_is_refused(kids_on):
    assert not kids.allows(item("No", genres=["Family"], adult=True))


def test_a_certification_above_the_ceiling_is_refused(kids_on):
    assert not kids.allows(item("R Film", genres=["Comedy"], mpaa="R"))
    assert not kids.allows(item("Mature", genres=["Comedy"], mpaa="TV-MA"))


def test_a_certification_under_the_ceiling_is_allowed(kids_on):
    assert kids.allows(item("G Film", genres=["Comedy"], mpaa="G"))
    assert kids.allows(item("PG Film", genres=["Comedy"], mpaa="PG"))


def test_the_ceiling_follows_the_age_setting(kids_on, settings_module):
    teen = item("Teen Film", genres=["Comedy"], mpaa="PG-13")
    settings_module.set("kids.age", "young")
    assert not kids.allows(teen)
    settings_module.set("kids.age", "teen")
    assert kids.allows(teen)


def test_a_prefixed_certification_still_parses(kids_on):
    """TMDB and Trakt both emit "US:R" as well as plain "R"."""
    assert not kids.allows(item("x", genres=["Comedy"], mpaa="US:R"))
    assert kids.allows(item("y", genres=["Comedy"], mpaa="US:G"))


def test_an_unknown_certification_is_not_the_thing_that_blocks(kids_on):
    """List results carry no rating, so the rows must carry the safety."""
    assert kids.allows(item("Unrated", genres=["Family"], mpaa=""))
    assert kids.allows(item("Odd", genres=["Family"], mpaa="NOT-A-RATING"))


def test_nothing_is_filtered_when_the_mode_is_off(settings_module):
    settings_module.set("kids.enabled", "false")
    nasty = item("A Nasty Film", genres=["Horror"], mpaa="R")
    assert kids.allows(nasty)
    assert kids.filter_items([nasty]) == [nasty]


def test_filtering_a_row_drops_only_the_blocked_items(kids_on):
    good = item("Nice", genres=["Family"])
    bad = item("Nasty", genres=["Horror"])
    assert kids.filter_items([good, bad, good]) == [good, good]


def test_peek_still_says_none_when_a_row_was_never_warmed(settings_module):
    """None means "not warmed" and is what makes the window fetch it.

    Filtering the cache read turned a miss into an empty list, so the home
    window stopped falling back to a live load and every unwarmed row stayed
    permanently blank. Found by opening the real Kodi, not by the suite.
    """
    settings_module.set("kids.enabled", "false")
    assert catalog.peek("trending_movies") is None

    settings_module.set("kids.enabled", "true")
    assert catalog.peek("trending_movies") is None


def test_peek_returns_a_list_once_the_row_is_warmed(kids_on):
    from katan import cache
    cache.set(catalog.cache_key("trending_movies"),
              [item("Nice", genres=["Family"])], 3600)
    assert catalog.peek("trending_movies") != []
    assert isinstance(catalog.peek("trending_movies"), list)


def test_a_row_warmed_before_kids_mode_is_still_filtered(kids_on):
    """Turning the mode on must not be defeated by a warm cache."""
    from katan import cache
    cache.set(catalog.cache_key("trending_movies"),
              [item("Nasty", genres=["Horror"]), item("Nice", genres=["Family"])],
              3600)
    shown = catalog.peek("trending_movies")
    assert [i["title"] for i in shown] == ["Nice"]


# --------------------------------------------------------------------------
# the PIN
# --------------------------------------------------------------------------


def test_the_pin_is_not_stored_in_the_clear(settings_module):
    kids.set_pin("1234")
    stored = settings_module.get("kids.pin_hash")
    assert stored and "1234" not in stored
    assert len(stored) == 64, "expected a sha256 digest"


def test_the_right_pin_turns_it_off(kids_on):
    kids.set_pin("1234")
    kids.turn_on()
    assert kids.turn_off("1234") is True
    assert not kids.enabled()


def test_the_wrong_pin_does_not(kids_on):
    kids.set_pin("1234")
    kids.turn_on()
    assert kids.turn_off("9999") is False
    assert kids.enabled(), "kids mode must survive a wrong PIN"


def test_no_pin_set_means_it_can_be_turned_off(kids_on):
    kids.set_pin("")
    kids.turn_on()
    assert kids.turn_off("") is True


def test_setting_an_empty_pin_clears_it(settings_module):
    kids.set_pin("1234")
    assert kids.has_pin()
    kids.set_pin("")
    assert not kids.has_pin()


# --------------------------------------------------------------------------
# settings hygiene
# --------------------------------------------------------------------------


def test_the_settings_have_defaults():
    for key in ("kids.enabled", "kids.age", "kids.pin_hash"):
        assert key in settings.DEFAULTS


def test_kids_mode_is_off_by_default():
    assert settings.DEFAULTS["kids.enabled"] == "false"


# --------------------------------------------------------------------------
# the Israeli kids row
# --------------------------------------------------------------------------


def test_the_israeli_row_takes_the_broadcasters_own_childrens_section():
    """Kan curates a children's podcast section. That is the signal used."""
    from katan import catalog

    found = catalog._kids_israel()
    assert found, "the row should not be empty"
    titles = [item["title"] for item in found]
    assert u"היסטוריה לילדים" in titles


def test_a_programme_about_children_is_not_a_programme_for_children():
    """A name describes the subject; only a section describes the audience.

    Matching names put "לא לפני הילדים", "מחפשת תשובה - חינוך ילדים" and
    "הילדים האבודים" into a row for small children - three adult programmes
    that happen to be about children.
    """
    from katan import catalog

    titles = [item["title"] for item in catalog._kids_israel()]
    for adult in (u"לא לפני הילדים", u"הילדים האבודים",
                  u"מחפשת תשובה - חינוך ילדים"):
        assert adult not in titles, adult


def test_a_three_letter_substring_is_not_a_word():
    """"הופ" for the Hop! channel also matched "הופעה" (performance) and
    "הופקר" (abandoned), which is how a documentary about 7 October reached a
    row for small children."""
    from katan import catalog

    assert u"הופ" not in catalog.KIDS_CATEGORY_WORDS
    assert u"הופ" not in catalog.KIDS_CHANNEL_WORDS
    titles = [item["title"] for item in catalog._kids_israel()]
    assert u"נחל עוז - המוצב שהופקר" not in titles


def test_the_row_stays_within_its_limit():
    from katan import catalog

    assert len(catalog._kids_israel()) <= catalog.ROW_LIMIT


# --------------------------------------------------------------------------
# a row that came back empty
# --------------------------------------------------------------------------


def test_an_empty_row_is_remembered_as_empty_not_as_unknown(monkeypatch):
    """peek() has to tell "never warmed" from "warmed and empty".

    Not caching an empty answer at all left the two indistinguishable, so the
    home listing kept offering a row that opens an empty screen - the anime
    row, for as long as AniList has been refusing requests.
    """
    from katan import catalog

    row_id = catalog.enabled_row_ids()[0]
    monkeypatch.setitem(catalog.by_id(row_id), "loader", lambda: [])

    assert catalog.peek(row_id) is None, "cold to begin with"
    assert catalog.load(row_id) == []
    assert catalog.peek(row_id) == [], "now known to be empty"


def test_an_empty_row_is_forgotten_again_quickly(monkeypatch):
    """A service that comes back in five minutes must not stay hidden for a
    day, so the empty answer has its own short TTL rather than the row's.

    And never longer than the row's own: continue-watching refreshes every
    five minutes, so remembering "nothing here" for ten would have made it
    slower to notice an empty row than a full one.
    """
    from katan import cache, catalog

    assert catalog.TTL_EMPTY <= 15 * 60

    written = []
    monkeypatch.setattr(cache, "set",
                        lambda key, value, ttl: written.append((key, ttl)))
    for row in catalog.rows():
        monkeypatch.setitem(row, "loader", lambda: [])
        catalog.load(row["id"], refresh=True)
    for key, ttl in written:
        assert ttl <= catalog.TTL_EMPTY, key
    assert written, "expected every row to record an empty answer"

    fast = min(row["ttl"] for row in catalog.rows())
    assert any(ttl == min(catalog.TTL_EMPTY, fast) for _key, ttl in written)
