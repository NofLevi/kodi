"""Settings and accounts to a file and back: an uninstall, a reset box, or a
second box to set up should not mean typing every key again with a remote."""
import json
import os

import pytest


def test_every_account_and_changed_setting_comes_back(settings_module):
    from pinky import backup, bookmarks
    settings_module.set_many({"torbox.apikey": "TB-KEY", "trakt.access_token": "TR-ACCESS",
                              "trakt.refresh_token": "TR-REFRESH", "subs.ai.gemini_key": "AI-KEY",
                              "subs.ktuvit.user": "me@example.com"})
    bookmarks.save("movie:tmdb:361743", 82.0, 7800.0)
    data = json.loads(json.dumps(backup.contents()))     # as it would be on disk

    for key in ("torbox.apikey", "trakt.access_token", "trakt.refresh_token",
                "subs.ai.gemini_key", "subs.ktuvit.user"):
        settings_module.set(key, "")
    bookmarks.clear("movie:tmdb:361743")

    assert backup.restore(data) == len(data["settings"])
    assert settings_module.get("torbox.apikey") == "TB-KEY"
    assert settings_module.get("trakt.refresh_token") == "TR-REFRESH"
    assert settings_module.get("subs.ai.gemini_key") == "AI-KEY"
    assert bookmarks.all_entries()["movie:tmdb:361743"]["position"] == 82.0


def test_only_what_was_changed_is_written_and_never_a_button(settings_module):
    from pinky import backup
    settings_module.set("torbox.apikey", "TB-KEY")
    written = backup.contents()["settings"]
    assert written == {"torbox.apikey": "TB-KEY"}
    assert not any(key.startswith("action.") for key in written)


def test_a_backup_from_an_older_version_still_restores(settings_module):
    """A setting it names may since have gone; the rest still comes back."""
    from pinky import backup
    count = backup.restore({"pinky_backup": backup.FORMAT, "settings": {
        "torbox.apikey": "TB-KEY", "a.setting.that.went": "x", "action.setup": "x"}})
    assert count == 1 and settings_module.get("torbox.apikey") == "TB-KEY"


@pytest.mark.parametrize("bad", [None, [], {}, {"pinky_backup": 99}, {"settings": {}}])
def test_anything_else_is_refused(bad, settings_module):
    from pinky import backup
    with pytest.raises(ValueError):
        backup.restore(bad)


def test_the_newer_resume_point_wins(settings_module):
    from pinky import bookmarks
    bookmarks.save("movie:tmdb:1", 500.0, 6000.0)
    now = bookmarks.all_entries()["movie:tmdb:1"]["at"]
    bookmarks.merge({"movie:tmdb:1": {"position": 100.0, "total": 6000.0, "at": now - 50},
                     "movie:tmdb:2": {"position": 300.0, "total": 6000.0, "at": now - 50}})
    entries = bookmarks.all_entries()
    assert entries["movie:tmdb:1"]["position"] == 500.0, "the place on this box was newer"
    assert entries["movie:tmdb:2"]["position"] == 300.0


def test_export_and_import_through_a_chosen_folder(settings_module, monkeypatch, tmp_path):
    import xbmcgui
    from pinky import backup, kodi
    settings_module.set("torbox.apikey", "TB-KEY")
    monkeypatch.setattr(xbmcgui.Dialog, "browse",
                        lambda self, kind, heading, shares, mask="", *a, **k: str(tmp_path) + os.sep,
                        raising=False)
    said = []
    monkeypatch.setattr(kodi, "ok_dialog", lambda message, *a, **k: said.append(message))
    target = backup.export_to_file()
    assert target and os.path.isfile(target)
    assert "TB-KEY" in open(target, encoding="utf-8").read()
    assert kodi.localize(32567, os.path.basename(target)) == said[-1], "says it holds the keys"

    settings_module.set("torbox.apikey", "")
    monkeypatch.setattr(xbmcgui.Dialog, "browse",
                        lambda self, kind, heading, shares, mask="", *a, **k: target, raising=False)
    monkeypatch.setattr(kodi, "yes_no", lambda *a, **k: True)
    assert backup.import_from_file() == 1
    assert settings_module.get("torbox.apikey") == "TB-KEY"


def test_importing_asks_first_and_refuses_another_file(settings_module, monkeypatch, tmp_path):
    import xbmcgui
    from pinky import backup, kodi
    other = tmp_path / "notes.json"
    other.write_text('{"hello": 1}', encoding="utf-8")
    monkeypatch.setattr(xbmcgui.Dialog, "browse",
                        lambda self, *a, **k: str(other), raising=False)
    said = []
    monkeypatch.setattr(kodi, "ok_dialog", lambda message, *a, **k: said.append(message))
    assert backup.import_from_file() == 0
    assert said == [kodi.localize(32571)]

    good = tmp_path / "pinky-backup.json"
    good.write_text(json.dumps({"pinky_backup": backup.FORMAT,
                                "settings": {"torbox.apikey": "NEW"}}), encoding="utf-8")
    monkeypatch.setattr(xbmcgui.Dialog, "browse",
                        lambda self, *a, **k: str(good), raising=False)
    monkeypatch.setattr(kodi, "yes_no", lambda *a, **k: False)
    settings_module.set("torbox.apikey", "MINE")
    assert backup.import_from_file() == 0
    assert settings_module.get("torbox.apikey") == "MINE", "nothing changes without a yes"
