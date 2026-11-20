"""Store: autosave, append-only history, restore, trash (C16-C18)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from draft_studio.store import DraftNotFound, Store, default_data_dir


def test_default_data_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAFT_STUDIO_DATA", str(tmp_path / "x"))
    assert default_data_dir() == tmp_path / "x"
    monkeypatch.delenv("DRAFT_STUDIO_DATA")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert default_data_dir() == tmp_path / ".local/share/draft-studio"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert default_data_dir() == tmp_path / "xdg/draft-studio"


def test_create_list_open(store: Store) -> None:
    mail = store.create("mail")
    text = store.create("text")
    ids = [d["id"] for d in store.list_drafts()]
    assert set(ids) == {mail["id"], text["id"]}
    state = store.get(mail["id"])
    assert state["meta"]["kind"] == "mail"
    assert state["text"] == ""
    assert state["claude"] == []
    assert (store.root / "drafts" / mail["id"] / "draft.md").read_text() == ""
    with pytest.raises(ValueError):
        store.create("poem")


def test_save_writes_markdown_and_history(store: Store) -> None:
    d = store.create("text")
    store.save(d["id"], {"text": "Hello", "claude": [[0, 5]]})
    store.save(d["id"], {"text": "Hello", "claude": [[0, 5]]})  # no-op
    store.save(d["id"], {"text": "Hello you", "claude": [[0, 5]]}, kind="edit")
    assert (store.root / "drafts" / d["id"] / "draft.md").read_text() == "Hello you"
    versions = store.history(d["id"])
    assert [v["v"] for v in versions] == [0, 1, 2]
    assert versions[-1]["text"] == "Hello you"


def test_save_fields(store: Store) -> None:
    d = store.create("mail")
    store.save(
        d["id"], {"to": "a@b.c", "subject": "Hi", "toAuthor": "you", "subjectAuthor": "you", "lang": "pl"}
    )
    meta = store.get(d["id"])["meta"]
    assert (meta["to"], meta["subject"], meta["subjectAuthor"], meta["lang"]) == ("a@b.c", "Hi", "you", "pl")
    with pytest.raises(ValueError):
        store.save(d["id"], {"subjectAuthor": "robot"})
    with pytest.raises(ValueError):
        store.save(d["id"], {"claude": [[3, 1]]})
    with pytest.raises(ValueError):
        store.save(d["id"], {"text": 5})


def test_restore_appends_version(store: Store) -> None:
    d = store.create("text")
    store.save(d["id"], {"text": "one", "claude": []})
    store.save(d["id"], {"text": "two", "claude": [[0, 3]]})
    state = store.restore(d["id"], 1)
    assert state["text"] == "one"
    versions = store.history(d["id"])
    assert versions[-1]["type"] == "restore"
    assert versions[-1]["from"] == 1
    assert len(versions) == 4
    with pytest.raises(KeyError):
        store.restore(d["id"], 99)


def test_ask_saved_in_history_and_meta(store: Store) -> None:
    d = store.create("text")
    store.add_ask(d["id"], {"from": 0, "to": 2, "span": "He", "question": "why?", "answer": "because"})
    assert store.get(d["id"])["meta"]["asks"][0]["answer"] == "because"
    assert store.history(d["id"])[-1]["type"] == "ask"
    assert store.get(d["id"])["text"] == ""


def test_update_meta(store: Store) -> None:
    d = store.create("mail")
    store.update_meta(d["id"], gmailDraftId="r-1")
    assert store.get(d["id"])["meta"]["gmailDraftId"] == "r-1"


def test_delete_moves_to_trash(store: Store) -> None:
    d = store.create("text")
    store.save(d["id"], {"text": "keep me"})
    store.delete(d["id"])
    assert store.list_drafts() == []
    trashed = list((store.root / "trash").iterdir())
    assert len(trashed) == 1
    assert (trashed[0] / "draft.md").read_text() == "keep me"
    with pytest.raises(DraftNotFound):
        store.get(d["id"])


def test_bad_ids_rejected(store: Store) -> None:
    for bad in ["../etc", "", "a/b", "trash"]:
        with pytest.raises(DraftNotFound):
            store.get(bad)


def test_list_skips_broken(store: Store) -> None:
    d = store.create("text")
    (store.root / "drafts" / "junk").mkdir()
    (store.root / "drafts" / d["id"] / "meta.json").write_text("{bad")
    assert store.list_drafts() == []


def test_history_file_is_jsonl(store: Store) -> None:
    d = store.create("text")
    lines = (store.root / "drafts" / d["id"] / "history.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["type"] == "create"


def test_restore_of_identical_version_adds_nothing(store: Store) -> None:
    d = store.create("text")
    store.save(d["id"], {"text": "same"})
    store.restore(d["id"], 1)
    assert [v["type"] for v in store.history(d["id"])] == ["create", "edit"]
    with pytest.raises(ValueError):
        store.save(d["id"], {"claude": "nope"})
