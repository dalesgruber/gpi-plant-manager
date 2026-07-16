"""Kiosk t() helper: approved English and Spanish-primary render modes."""
from __future__ import annotations

import pytest

from zira_dashboard import timeclock_i18n


class _Ctx(dict):
    """Stand-in for a Jinja context object (supports .get)."""


def _render(text, mode="en", **kwargs):
    return timeclock_i18n.t(
        _Ctx(timeclock_language=mode), text, **kwargs
    )


def test_level_three_selects_spanish_primary():
    assert (
        timeclock_i18n.language_mode_for_person({"spanish_level": 3})
        == "es_primary"
    )


@pytest.mark.parametrize("value", [None, 0, 1, 2, 3.0, 4, "3", True])
def test_every_other_value_selects_english(value):
    assert (
        timeclock_i18n.language_mode_for_person({"spanish_level": value})
        == "en"
    )


def test_context_for_person_includes_timeclock_language():
    assert timeclock_i18n.context_for_person({"spanish_level": 3}) == {
        "timeclock_language": "es_primary"
    }


def test_spanish_primary_stacks_spanish_then_small_english():
    out = str(_render("Clock Out", "es_primary"))
    assert '<span class="k-es k-primary">Marcar salida</span>' in out
    assert '<span class="k-en k-secondary">Clock Out</span>' in out
    assert out.index("k-es k-primary") < out.index("k-en k-secondary")


def test_english_mode_is_plain_english():
    assert _render("Clock Out", "en") == "Clock Out"


def test_unknown_spanish_key_falls_back_to_english_only():
    assert _render("Totally unknown label", "es_primary") == "Totally unknown label"


def test_format_substitution_both_languages():
    out = str(_render("Since {time}", "es_primary", time="2:30 PM"))
    assert "Since 2:30 PM" in out
    assert "Desde 2:30 PM" in out


def test_substituted_value_is_escaped():
    out = str(_render("Since {time}", "es_primary", time="<x>"))
    assert "<x>" not in out
    assert "&lt;x&gt;" in out


def test_every_translation_value_is_nonempty():
    assert all(v.strip() for v in timeclock_i18n.TRANSLATIONS.values())
