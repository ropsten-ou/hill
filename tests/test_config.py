import json

import pytest

from hill import config


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home of the test's own, palace's settings in it, no $PALACE_VAULT,
    and a terminal that answers what `answers` holds."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("PALACE_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PALACE_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_terminal", lambda: True)
    answers = []
    monkeypatch.setattr("builtins.input", lambda prompt: answers.pop(0))
    return tmp_path / "home", answers


def saved(tmp_path):
    return json.loads((tmp_path / "config" / "settings.json").read_text())["folder"]


def test_without_projects_the_default_is_the_current_folder(home, tmp_path):
    assert config.vault_path() == tmp_path
    (home[0] / "projects").mkdir()
    assert config.vault_path() == home[0] / "projects"


def test_the_first_start_asks_once_and_ret_takes_the_default(home, tmp_path):
    folder, answers = home
    (folder / "projects").mkdir()
    answers.append("")
    config.ask_folder()
    assert saved(tmp_path) == "~/projects"
    config.ask_folder()  # not again: input would fail with no answer left
    assert config.vault_path() == folder / "projects"


def test_an_answer_that_isnt_a_folder_is_asked_again(home, tmp_path, capsys):
    folder, answers = home
    (folder / "notes").mkdir()
    answers.extend(["~/nowhere", "~/notes"])
    config.ask_folder()
    assert "isn't a folder" in capsys.readouterr().out
    assert saved(tmp_path) == "~/notes" and config.vault_path() == folder / "notes"


def test_palace_vault_wins_and_isnt_asked(home, monkeypatch, tmp_path):
    monkeypatch.setenv("PALACE_VAULT", str(tmp_path))
    config.ask_folder()
    assert not (tmp_path / "config" / "settings.json").exists()
    assert config.vault_path() == tmp_path


def test_without_projects_ret_makes_notes_from_the_starter(home, tmp_path, capsys):
    folder, answers = home
    answers.append("")
    config.ask_folder()
    assert "a few notes to try palace on" in capsys.readouterr().out
    assert saved(tmp_path) == "~/notes" and config.vault_path() == folder / "notes"
    assert (folder / "notes" / "Home.md").is_file()
    assert (folder / "notes" / "garden" / "work" / "001-raised-beds-sample.md").is_file()


def test_an_existing_notes_folder_is_offered_as_it_is(home, tmp_path):
    folder, answers = home
    (folder / "notes").mkdir()
    answers.append("")
    config.ask_folder()
    assert saved(tmp_path) == "~/notes" and list((folder / "notes").iterdir()) == []
