# -*- coding: utf-8 -*-
"""Yify, matched to the release names its sister site YTS mints.

Probed live on 28 September 2026, because `yifysubtitles.ch` - the obvious
domain - answers search fine and then refuses every actual download with
Cloudflare's bot challenge (403, `Cf-Mitigated: challenge`), which nothing in
this add-on can pass. `yts-subs.com` carries the identical catalogue with no
such wall, and its download URL turned out to need no extra request at all:
it is the search page's own slug, moved to `subtitles.yts-subs.com`, with
`.zip` appended.

A real captured row is what these fixtures are built from, trimmed to one
`<tr>` block rather than the fifty-odd a popular film actually returns.
"""
import pytest


@pytest.fixture
def provider():
    from pinky.subs.providers import yify
    return yify


# One real row, trimmed from a live capture of
# https://yts-subs.com/movie-imdb/tt5700672 (Train to Busan). The rating and
# uploader cells are kept because the row parser has to skip past them to
# reach the language, slug and release name.
_ROW = """
<tr data-id="158024" class="high-rating">
    <td class="rating-cell">
        <span class="label ">0</span>
    </td>
    <td class="flag-cell">
        <span class="flag flag-sa"></span>
        <span class="sub-lang">Arabic</span>
    </td>
    <td>
        <a href="/subtitles/train-to-busan-2016-arabic-yify-35153">
        <span class="text-muted">subtitle</span> Train.to.Busan.2016.720p.BluRay.x264.[YTS.AG]</a>
    </td>
    <td class="uploader-cell">sub</td>
    <td class="download-cell">
        <a href="/subtitles/train-to-busan-2016-arabic-yify-35153" class="subtitle-download">download</a>
    </td>
</tr>
"""

# The same row with the language name lowercased, because the site's own
# template is not consistent about it between pages - measured, one page
# wrote "english" and another "Arabic" on the same day.
_ROW_LOWERCASE = _ROW.replace(">Arabic<", ">arabic<")


class _Response(object):
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _serving(monkeypatch, provider, html, status_code=200):
    seen = {}

    def get(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return _Response(html, status_code)

    monkeypatch.setattr(provider.http, "get", get)
    return seen


MOVIE = {"type": "movie", "ids": {"imdb": "tt5700672"}, "title": "Train to Busan"}


def test_a_row_is_parsed_into_a_candidate(monkeypatch, provider):
    _serving(monkeypatch, provider, "<table><tbody>%s</tbody></table>" % _ROW)
    found = provider.search(MOVIE, None, ["ar"])
    assert len(found) == 1
    candidate = found[0]
    assert candidate["language"] == "ar"
    assert candidate["release"] == "Train.to.Busan.2016.720p.BluRay.x264.[YTS.AG]"
    assert candidate["download"] == (
        "https://subtitles.yts-subs.com/subtitles/"
        "train-to-busan-2016-arabic-yify-35153.zip")


def test_the_language_name_is_read_case_insensitively(monkeypatch, provider):
    """The site's own template is not consistent about casing between pages -
    measured, one page wrote "english" and another "Arabic" on the same day.
    A scan that only matched one case is exactly the mistake an earlier
    survey of this site made, and it read as "nothing here" on a page that
    had real subtitles on it."""
    _serving(monkeypatch, provider, "<table><tbody>%s</tbody></table>" % _ROW_LOWERCASE)
    found = provider.search(MOVIE, None, ["ar"])
    assert len(found) == 1
    assert found[0]["language"] == "ar"


def test_only_requested_languages_are_returned(monkeypatch, provider):
    _serving(monkeypatch, provider, "<table><tbody>%s</tbody></table>" % _ROW)
    assert provider.search(MOVIE, None, ["he"]) == []
    assert provider.search(MOVIE, None, ["en", "pl"]) == []
    assert len(provider.search(MOVIE, None, ["ar", "en"])) == 1


def test_a_language_this_add_on_never_asks_for_is_skipped(monkeypatch, provider):
    """Bengali, Icelandic and the rest of the site's full list cost nothing
    to leave unmapped: a name with no entry is a language nobody requested."""
    row = _ROW.replace(">Arabic<", ">Icelandic<")
    _serving(monkeypatch, provider, "<table><tbody>%s</tbody></table>" % row)
    assert provider.search(MOVIE, None, ["he", "en", "ar"]) == []


def test_a_tv_series_is_never_asked(monkeypatch, provider):
    """Probed live: a series IMDb id answers 404 on this site. YTS itself
    never releases a show, only films, so asking would be a wasted request on
    every single episode of every series this add-on plays."""
    def fail(*a, **k):
        raise AssertionError("asked yify for a TV episode")

    monkeypatch.setattr(provider.http, "get", fail)
    episode = dict(MOVIE, type="episode", season=1, episode=1)
    assert provider.search(episode, None, ["en"]) == []


def test_no_imdb_id_is_never_asked(monkeypatch, provider):
    def fail(*a, **k):
        raise AssertionError("asked yify with no imdb id")

    monkeypatch.setattr(provider.http, "get", fail)
    assert provider.search({"type": "movie", "ids": {}}, None, ["en"]) == []


def test_none_of_the_requested_languages_are_ones_this_site_could_answer(
        monkeypatch, provider):
    """Asked only for a language never in `_LANGUAGE_NAMES` - the automatic
    path never does this, but a caller could - there is nothing to search
    for, so nothing is."""
    def fail(*a, **k):
        raise AssertionError("asked yify for a language it cannot serve")

    monkeypatch.setattr(provider.http, "get", fail)
    assert provider.search(MOVIE, None, ["vi"]) == []


def test_a_failed_search_answers_with_nothing_rather_than_raising(monkeypatch,
                                                                  provider):
    _serving(monkeypatch, provider, "", status_code=500)
    assert provider.search(MOVIE, None, ["en"]) == []


def test_the_search_url_carries_the_imdb_id(monkeypatch, provider):
    seen = _serving(monkeypatch, provider, "<table><tbody>%s</tbody></table>" % _ROW)
    provider.search(MOVIE, None, ["ar"])
    assert seen["url"] == "https://yts-subs.com/movie-imdb/tt5700672"


def test_download_unzips_the_real_shape(monkeypatch, provider):
    """The site serves a zip holding one .srt, exactly what `common` already
    unzips for every other provider - nothing provider-specific here."""
    import io
    import zipfile

    from pinky.subs.providers import common

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Train.to.Busan.2016.720p.BluRay.x264.[YTS.AG]-Arabic.srt",
                   "1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    monkeypatch.setattr(common, "fetch_bytes", lambda url, **k: archive.getvalue())

    candidate = {"download": "https://subtitles.yts-subs.com/x.zip", "language": "ar"}
    data = provider.download(candidate)
    assert b"Hello" in data


def test_supports_only_languages_this_add_on_would_ask_for(provider):
    assert provider.supports("he")
    assert provider.supports("en")
    assert not provider.supports("vi")
