"""Store: autosave, append-only history, restore, trash (C16-C18)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from draft_studio.store import (
    MAX_ASSET_BYTES,
    AssetError,
    AssetNotFound,
    DraftNotFound,
    Store,
    default_data_dir,
    sniff_image,
)
from tests.imgdata import GIF, JPEG, SVG, WEBP, png


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


# -- image assets ---------------------------------------------------------------
def test_sniff_image_types() -> None:
    assert sniff_image(png()) == "png"
    assert sniff_image(JPEG) == "jpg"
    assert sniff_image(GIF) == "gif"
    assert sniff_image(b"GIF87a" + b"\x00" * 8) == "gif"
    assert sniff_image(WEBP) == "webp"
    assert sniff_image(SVG) is None
    assert sniff_image(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None
    assert sniff_image(b"") is None


def test_add_asset_stores_under_draft(store: Store) -> None:
    d = store.create("text")
    name = store.add_asset(d["id"], "My Photo.png", png())
    assert name.startswith("My-Photo-") and name.endswith(".png")
    assert (store.root / "drafts" / d["id"] / "assets" / name).read_bytes() == png()
    data, ctype = store.asset(d["id"], name)
    assert (data, ctype) == (png(), "image/png")
    assert store.list_assets(d["id"]) == [{"name": name, "size": len(png()), "type": "image/png"}]


def test_add_asset_is_not_a_version(store: Store) -> None:
    d = store.create("text")
    store.save(d["id"], {"text": "hi"})
    store.add_asset(d["id"], "a.png", png())
    assert store.get(d["id"])["meta"]["version"] == 1
    assert [h.get("v") for h in store.history(d["id"]) if "text" in h] == [0, 1]
    assert store.history(d["id"])[-1]["type"] == "asset"


def test_add_asset_dedupes_by_content(store: Store) -> None:
    d = store.create("text")
    first = store.add_asset(d["id"], "one.png", png())
    again = store.add_asset(d["id"], "other-name.png", png())
    assert again == first
    other = store.add_asset(d["id"], "one.png", png(rgb=(1, 2, 3)))
    assert other != first
    assert len(store.list_assets(d["id"])) == 2


def test_asset_names_are_sanitised(store: Store) -> None:
    d = store.create("text")
    for i, raw in enumerate(
        ["../../etc/passwd.png", "..\\..\\win.png", "/abs/x.png", ".hidden.png", "a b?c*.png"]
    ):
        name = store.add_asset(d["id"], raw, png(rgb=(i, 0, 0)))
        assert "/" not in name and "\\" not in name and not name.startswith(".")
        assert (store.root / "drafts" / d["id"] / "assets" / name).is_file()
    assert store.add_asset(d["id"], "", png(rgb=(9, 9, 9))).startswith("image-")
    assert store.add_asset(d["id"], "żółw.png", png(rgb=(8, 8, 8))).startswith("w-")
    assert store.add_asset(d["id"], "ąę.png", png(rgb=(6, 6, 6))).startswith("image-")
    long = store.add_asset(d["id"], "x" * 300 + ".png", png(rgb=(7, 7, 7)))
    assert len(long) <= 100
    assert not (store.root / "etc").exists()


def test_extension_follows_sniffed_type(store: Store) -> None:
    d = store.create("text")
    assert store.add_asset(d["id"], "photo.png", JPEG).endswith(".jpg")
    assert store.add_asset(d["id"], "photo.jpeg", JPEG + b"1").endswith(".jpeg")
    assert store.add_asset(d["id"], "anim", GIF).endswith(".gif")
    assert store.add_asset(d["id"], "w.WEBP", WEBP).endswith(".webp")


def test_asset_rejects_bad_type_and_size(store: Store) -> None:
    d = store.create("text")
    with pytest.raises(AssetError) as bad:
        store.add_asset(d["id"], "x.svg", SVG)
    assert bad.value.code == "bad-image"
    with pytest.raises(AssetError) as big:
        store.add_asset(d["id"], "big.png", png() + b"\x00" * MAX_ASSET_BYTES)
    assert big.value.code == "too-large"
    assert store.list_assets(d["id"]) == []


def test_asset_lookup_refuses_traversal(store: Store) -> None:
    d = store.create("text")
    store.save(d["id"], {"text": "secret"})
    for bad in ["../draft.md", "..", "meta.json", "../meta.json", ".x.png", "a/b.png", "nope.png", ""]:
        with pytest.raises(AssetNotFound):
            store.asset(d["id"], bad)
    with pytest.raises(DraftNotFound):
        store.asset("../etc", "x.png")
    with pytest.raises(DraftNotFound):
        store.add_asset("missing00", "x.png", png())


def test_asset_with_wrong_bytes_on_disk_is_not_served(store: Store) -> None:
    d = store.create("text")
    assets = store.root / "drafts" / d["id"] / "assets"
    assets.mkdir()
    (assets / "evil.png").write_bytes(SVG)
    with pytest.raises(AssetNotFound):
        store.asset(d["id"], "evil.png")


def test_list_assets_skips_junk(store: Store) -> None:
    d = store.create("text")
    assert store.list_assets(d["id"]) == []
    name = store.add_asset(d["id"], "a.png", png())
    assets = store.root / "drafts" / d["id"] / "assets"
    (assets / "half.png.tmp").write_bytes(b"x")
    (assets / "notes.txt").write_text("x")
    (assets / "sub.png").mkdir()
    (assets / "fake.png").write_text("not an image")
    assert [a["name"] for a in store.list_assets(d["id"])] == [name]


def test_short_hash_clash_never_overwrites(store: Store) -> None:
    d = store.create("text")
    data = png(rgb=(5, 5, 5))
    short = hashlib.sha256(data).hexdigest()[:12]
    assets = store.root / "drafts" / d["id"] / "assets"
    assets.mkdir()
    squatter = assets / f"a-{short}.png"
    squatter.write_bytes(png(rgb=(6, 6, 6)))
    name = store.add_asset(d["id"], "a.png", data)
    assert name == f"{hashlib.sha256(data).hexdigest()}.png"
    assert squatter.read_bytes() == png(rgb=(6, 6, 6))
    assert store.asset(d["id"], name)[0] == data


def test_trash_and_restore_keep_assets(store: Store) -> None:
    d = store.create("text")
    name = store.add_asset(d["id"], "a.png", png())
    store.save(d["id"], {"text": f"![](assets/{name})"})
    store.save(d["id"], {"text": "gone"})
    store.restore(d["id"], 1)
    assert store.get(d["id"])["text"] == f"![](assets/{name})"
    assert store.asset(d["id"], name)[0] == png()
    store.delete(d["id"])
    (trashed,) = (store.root / "trash").iterdir()
    assert (trashed / "assets" / name).read_bytes() == png()
