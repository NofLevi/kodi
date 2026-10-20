# -*- coding: utf-8 -*-
"""A translated line written in the wrong alphabet.

Hikaru no Go, episode 1, translated from English on 14 October 2026 and read
back off the projector: 5,608 Hebrew characters, 44 Latin and 76 Arabic,
across 24 lines. Clean UTF-8 with a byte order mark throughout - the bytes
were never the problem. The model starts a name in Hebrew and finishes it in
Arabic, because transliterating Japanese names into Arabic is common enough
in the training data to win mid-word.
"""
import pytest

from pinky.subs.ai import translator

HIKARU = u"\u05d4\u05d9\u05e7\u0627\u0631\u0648"          # היק + ارو
FUJIWARA = u"\u05e4\u05d5\u05d2'\u064a\u0648\u0627\u0631\u0627"
EMPEROR = u"\u05d4\u05e7\u05d9\u05e1\u0631"               # final Arabic resh
CLEAN = u"\u05d4\u05d9\u05e7\u05d0\u05e8\u05d5, \u05d1\u05d5\u05d0 \u05e0\u05e6\u05d0"


@pytest.mark.parametrize("line", [HIKARU, FUJIWARA, EMPEROR])
def test_arabic_inside_a_hebrew_line_is_refused(line):
    assert translator.wrong_script(line, "Hebrew") is True


def test_clean_hebrew_passes():
    assert translator.wrong_script(CLEAN, "Hebrew") is False


def test_latin_and_digits_are_not_foreign():
    """A subtitle carries names, numbers and the odd English word on
    purpose. Refusing those would refuse most correct lines."""
    assert translator.wrong_script(u"\u05e9\u05dc\u05d5\u05dd Go 5-1",
                                   "Hebrew") is False


def test_an_empty_line_is_not_a_script_problem():
    assert translator.wrong_script("", "Hebrew") is False
    assert translator.wrong_script(None, "Hebrew") is False


def test_a_language_we_have_no_rule_for_is_left_alone():
    """Better to accept than to reject every line of a language nobody
    wrote a rule for."""
    assert translator.wrong_script(HIKARU, "Japanese") is False


def test_the_model_is_told_before_it_is_corrected():
    """Retrying costs a request; asking costs nothing."""
    assert "Hebrew letters only" in translator.HEBREW_GUIDANCE
    assert "Arabic" in translator.HEBREW_GUIDANCE
