# -*- coding: utf-8 -*-
"""A subtitle filed under our show that names another one.

Wizdom files an upload under every show whose title contains its name, so
Gilmore Girls is answered with HBO's Girls and CSI: Miami with CSI - with the
right season and episode on them, which is all the matcher used to ask.
"""
import pytest

from pinky.meta import tmdb
from pinky.subs import othershow

# What TMDB answers for each name, as (id, name, original name, first year).
TMDB = {
    "girls": [("42282", "Girls", "Girls", "2012"),
              ("4586", "Gilmore Girls", "Gilmore Girls", "2000")],
    "csi": [("1620", "CSI: Miami", "CSI: Miami", "2002"),
            ("1431", "CSI: Crime Scene Investigation",
             "CSI: Crime Scene Investigation", "2000"),
            ("2458", "CSI: NY", "CSI: NY", "2004")],
    "csi ny": [("2458", "CSI: NY", "CSI: NY", "2004")],
    "the middle": [("11111", "The Middle", "The Middle", "2009")],
    "lost girl": [("33852", "Lost Girl", "Lost Girl", "2010"),
                  ("4607", "Lost", "Lost", "2004")],
    "buffy": [("95", "Buffy the Vampire Slayer", "Buffy the Vampire Slayer", "1997")],
    "lois and clark": [("2567", "Lois & Clark: The New Adventures of Superman",
                        "Lois & Clark: The New Adventures of Superman", "1993")],
    "stargate atlantis": [("2290", "Stargate Atlantis", "Stargate Atlantis", "2004")],
}


@pytest.fixture
def asked(monkeypatch):
    questions = []

    def shows_named(query):
        questions.append(query.lower())
        return TMDB.get(query.lower(), [])
    monkeypatch.setattr(tmdb, "shows_named", shows_named)
    return questions


def show(title, tmdb_id, year, **more):
    meta = {"type": "episode", "title": title, "show_title": title, "year": year,
            "ids": {"tmdb": tmdb_id}, "season": 6, "episode": 5, "extra": {}}
    meta.update(more)
    return meta


def kept(names, meta):
    return [c["release"] for c in othershow.drop([{"release": n} for n in names], meta)]


def test_another_show_sharing_a_word_is_dropped(asked):
    """Gilmore Girls 6x05: "Girls.S06E05.720p.HDTV.x264-AVS" was the best
    Hebrew subtitle on offer, at 99%, ahead of the real one at 85."""
    names = ["Gilmore.Girls.S06E05.HDTV.XviD-LOL", "Girls.S06E05.720p.HDTV.x264-AVS",
             "Gilmore.Girls.HEBSub.S06E05.HEB", "Girls.S06E05.HDTV.x264-SVA"]
    assert kept(names, show("Gilmore Girls", 4586, 2000)) == [
        "Gilmore.Girls.S06E05.HDTV.XviD-LOL", "Gilmore.Girls.HEBSub.S06E05.HEB"]
    assert asked == ["girls"]            # once, and never for our own name


@pytest.mark.parametrize("title, tmdb_id, year, name", [
    ("Malcolm in the Middle", 2004, 2000, "The.Middle.S01E03.HDTV.XviD-FQM"),
    ("Lost", 4607, 2004, "Lost.Girl.S01E03.HDTV.XviD-2HD"),
    ("CSI: Crime Scene Investigation", 1431, 2000, "CSI.NY.S02E03.HDTV.XviD-LOL"),
    ("Stargate SG-1", 4629, 1997, "Stargate.Atlantis.S01E03.DVDRip.XviD"),
])
def test_the_shows_wizdom_files_under_another(asked, title, tmdb_id, year, name):
    assert kept([name], show(title, tmdb_id, year,
                             translated_titles=["Stargate"])) == []


@pytest.mark.parametrize("title, tmdb_id, year, name", [
    ("Buffy the Vampire Slayer", 95, 1997, "Buffy.S02E03.DVDRip.XviD"),
    ("Lois & Clark: The New Adventures of Superman", 2567, 1993,
     "Lois.and.Clark.S02E03.DVDRip"),
    ("Hawaii Five-0", 32798, 2010, "Hawaii.Five-0.2010.S01E03.HDTV-LOL"),
    ("Grey's Anatomy", 1416, 2005, "Greys.Anatomy.S09E03.HDTV.x264-LOL"),
    ("The X-Files", 4087, 1993, "X-Files.S02E03.DVDRip"),
    ("Friends", 1668, 1994, "Druzja.s05e03.DVDRip"),
])
def test_a_shorter_or_stranger_spelling_of_ours_is_kept(asked, title, tmdb_id, year, name):
    """Words alone cannot tell "Buffy" from "Girls": nobody else goes by it."""
    assert kept([name], show(title, tmdb_id, year)) == [name]


def test_a_franchise_name_is_the_first_shows(asked):
    """"CSI.S09E13" is CSI: Crime Scene Investigation, and was the only Hebrew
    subtitle offered for CSI: Miami 9x13 - at 99%."""
    name = "CSI.S09E13.720p.HDTV.x264-CTU"
    assert kept([name], show("CSI: Miami", 1620, 2002)) == []
    assert kept([name], show("CSI: Crime Scene Investigation", 1431, 2000)) == [name]


def test_a_name_that_only_folds_to_ours_does_not_make_it_ours(asked):
    """A Japanese title of CSI: Miami loses its Japanese and is left "csi"."""
    meta = show("CSI: Miami", 1620, 2002,
                translated_titles=[u"CSI:マイアミ"])
    assert kept(["CSI.S09E13.720p.HDTV.x264-CTU"], meta) == []


def test_a_hash_match_is_the_file_whatever_it_is_called(asked):
    candidate = {"release": "Girls.S06E05.720p.HDTV.x264-AVS", "hash_match": True}
    assert othershow.drop([candidate], show("Gilmore Girls", 4586, 2000)) == [candidate]
    assert asked == []


def test_films_and_anime_are_left_alone(asked):
    names = [{"release": "Girls.S06E05.720p.HDTV.x264-AVS"}]
    assert othershow.drop(names, {"type": "movie", "title": "Gilmore Girls"}) == names
    assert othershow.drop(names, show("Gilmore Girls", 4586, 2000,
                                      extra={"anime": True})) == names
    assert asked == []


def test_it_asks_a_bounded_number_of_questions(asked):
    names = ["Show%d.S06E05.HDTV" % number for number in range(12)]
    assert len(kept(names, show("Gilmore Girls", 4586, 2000))) == 12
    assert len(asked) == othershow.MAX_ASKED


def test_a_search_that_fails_drops_nothing(monkeypatch):
    def broken(query):
        raise ValueError("no answer")
    monkeypatch.setattr(tmdb, "shows_named", broken)
    name = "Girls.S06E05.720p.HDTV.x264-AVS"
    assert kept([name], show("Gilmore Girls", 4586, 2000)) == [name]
