"""Transcript checks: every way a `claude -p` Gmail run can differ from its claim is refused."""

from __future__ import annotations

from typing import Any

import pytest

from draft_studio.gmail import GmailError
from draft_studio.mail import Mail
from tests.gmail_fixtures import (
    CREATE,
    DELETE,
    GET,
    PICS,
    PLAIN,
    UPDATE,
    Call,
    P,
    call,
    create,
    run,
    transcript,
)

NEW = {"id": "new", "created": True}
OLD = {"id": "r1", "created": False}


@pytest.mark.parametrize(
    "calls,result,mail,gid",
    [
        ([call(f"{P}send_message")], None, PLAIN, ""),
        ([create()], {"id": 1, "created": True}, PLAIN, ""),
        ([create()], {"id": "r1", "created": "yes"}, PLAIN, ""),
        ([], None, PLAIN, ""),
        ([create(), create(new_id="r2")], None, PLAIN, ""),  # two drafts
        # The connector refuses in a plain (non-error) result; the claimed id is not in it.
        ([call(CREATE, PICS.payload(), "files can't be attached")], {"id": "", "created": False}, PICS, ""),
        ([call(CREATE, PICS.payload(), "files can't be attached")], {"id": "", "created": True}, PICS, ""),
        (
            [call(GET, {"draftId": "r1"}), call(UPDATE, {"draftId": "r1", **PLAIN.payload()}, "refused")],
            {"id": "r1", "created": False},
            PLAIN,
            "r1",
        ),
        ([call(CREATE, {**PLAIN.payload(), "body": "B, reworded"}, '{"id": "r1"}')], None, PLAIN, ""),
        ([call(CREATE, {**PLAIN.payload(), "htmlBody": "<p>x</p>"}, '{"id": "r1"}')], None, PLAIN, ""),
        ([create(includeSignature=True)], None, PLAIN, ""),
        (
            [call(CREATE, {k: v for k, v in PICS.payload().items() if k != "attachments"}, '{"id": "r1"}')],
            None,
            PICS,
            "",
        ),
        ([call(CREATE, PLAIN.payload(), "quota", error=True)], None, PLAIN, ""),
        ([create(new_id="zzz")], None, PLAIN, ""),  # claimed id is not the one create_draft returned
        ([call(CREATE, PLAIN.payload(), None)], None, PLAIN, ""),  # no tool result at all
        ([create(), call(DELETE, {"draftId": "x"})], None, PLAIN, ""),  # delete outside the replace flow
        (
            [
                call(GET, {"draftId": "r1"}),
                call(UPDATE, {"draftId": "r1", **PLAIN.payload()}, '{"id": "r1"}'),
            ],
            {"id": "r1", "created": True},
            PLAIN,
            "r1",
        ),
        ([call(GET, {"draftId": "r1"}), create()], {"id": "r1", "created": False}, PLAIN, "r1"),
        (
            [
                call(GET, {"draftId": "r1"}),
                call(UPDATE, {"draftId": "other", **PLAIN.payload()}, '{"id": "other"}'),
            ],
            {"id": "other", "created": False},
            PLAIN,
            "r1",
        ),
        (
            [
                call(GET, {"draftId": "r1"}),
                call(UPDATE, {"draftId": "other", **PLAIN.payload()}, '{"id": "other"}'),
            ],
            OLD,
            PLAIN,
            "r1",
        ),
        (
            [
                call(GET, {"draftId": "r1"}),
                call(UPDATE, {"draftId": "r1", **PLAIN.payload()}, "x", error=True),
            ],
            OLD,
            PLAIN,
            "r1",
        ),
        ([create(new_id="new")], NEW, PLAIN, "r1"),  # created without checking the stored one
        ([call(GET, {"draftId": "zz"}), create(new_id="new")], NEW, PLAIN, "r1"),  # checked another id
        ([call(DELETE, {"draftId": "old"}), create(PICS, "new")], NEW, PICS, "old"),  # delete before create
        ([create(PICS, "new"), call(DELETE, {"draftId": "other"})], NEW, PICS, "old"),
        ([create(PICS, "new")], NEW, PICS, "old"),  # old one left behind
        (
            [create(PICS, "new"), call(DELETE, {"draftId": "old"}), call(DELETE, {"draftId": "old"})],
            NEW,
            PICS,
            "old",
        ),
        (
            [call(UPDATE, {"draftId": "old", **PICS.payload()}, '{"id": "old"}')],
            {"id": "old", "created": False},
            PICS,
            "old",
        ),
        (
            [create(PICS, "old"), call(DELETE, {"draftId": "old"})],
            {"id": "old", "created": True},
            PICS,
            "old",
        ),
    ],
)
def test_cli_rejects(
    monkeypatch: pytest.MonkeyPatch, calls: list[Call], result: dict[str, Any] | None, mail: Mail, gid: str
) -> None:
    with pytest.raises(GmailError):
        run(monkeypatch, transcript(*calls, result=result), mail=mail, gid=gid)
