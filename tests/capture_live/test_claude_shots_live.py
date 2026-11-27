"""Draft studio's own (fake) Claude asks for screenshots; real headless chromium takes them first.

Run: .venv/bin/pytest -q -m chromium tests/capture_live/test_claude_shots_live.py
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest

from draft_studio import server as srv
from draft_studio import shots
from draft_studio.claude_fake import FakeClaude
from draft_studio.gmail import FakeGmail
from tests.capture_live.conftest import CAPTURE_TIMEOUT, Rig, Site
from tests.capture_live.pngread import red_pixels
from tests.capture_live.test_capture_live import asset
from tests.conftest import App

pytestmark = pytest.mark.chromium


@pytest.fixture
def studio(rig: Rig) -> Iterator[App]:
    root = rig.store.root
    deps = srv.Deps(rig.store, FakeClaude(), FakeGmail(root / "gmail.json"), root, root, "live")
    httpd = srv.make_server(deps, port=0)
    httpd.captures = rig.service
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.02), daemon=True).start()
    app = App(f"http://127.0.0.1:{httpd.server_address[1]}", rig.store, deps.gmail, httpd)
    # One propose may run a capture plus MAX_FIXES corrected ones, each a fresh chromium: the
    # editor waits for it with no client timeout, so 5 s only measured how starved the CPU was.
    app.timeout = CAPTURE_TIMEOUT * (1 + shots.MAX_FIXES)
    yield app
    httpd.shutdown()
    httpd.server_close()


def propose(app: App, rig: Rig, text: str) -> tuple[int, Any]:
    rig.store.save(rig.draft, {"text": text, "brief": "Poradnik", "phase": "editing"})
    body = {"text": text, "from": 0, "to": len(text), "instruction": "[shot]"}
    return app.call("POST", f"/api/drafts/{rig.draft}/propose", body)


def image_of(rig: Rig, replacement: str) -> bytes:
    (name,) = [a["name"] for a in rig.store.list_assets(rig.draft) if f"(assets/{a['name']})" in replacement]
    return asset(rig, name)


def released(app: App, rig: Rig) -> dict[str, Any]:
    _, listing = app.call("GET", f"/api/drafts/{rig.draft}/held")
    assert listing["held"] == [], listing
    (out,) = listing["released"]
    return dict(out)


def test_proposal_arrives_with_a_real_highlighted_screenshot(site: Site, rig: Rig, studio: App) -> None:
    # „Pokaz szczegoly” is not on the page: chromium's `seen` labels let Claude fix it.
    text = f"Otworzyć {site.base}/index.html i kliknąć „Pokaz szczegoly”."
    status, prop = propose(studio, rig, text)
    assert status == 200, prop
    assert prop["replacement"].startswith(f"{text}\n\n![Pokaz szczegoly](assets/shot-1-"), prop
    width, height, red = red_pixels(image_of(rig, prop["replacement"]))
    assert (width, height) == (2560, 1600) and red > 3000, red
    assert prop["dropped"] == [] and "IMG" not in prop["replacement"]


def test_a_capture_needing_permission_is_held_then_granted(site: Site, rig: Rig, studio: App) -> None:
    text = f"Otworzyć {site.base}/index.html i kliknąć „Zapisz”."
    sent = site.writes["POST /api/save"]
    status, body = propose(studio, rig, text)
    assert status == 202, body
    (req,) = body["held"]["requests"]
    assert (req["reason"], req["detail"]) == ("request", f"POST {site.base}/api/save")
    assert site.writes["POST /api/save"] == sent  # held: nothing was sent
    assert studio.call("POST", f"/api/captures/{req['id']}/grant", {})[0] == 200
    assert site.writes["POST /api/save"] == sent + 1
    out = released(studio, rig)
    assert out["span"] == text and out["dropped"] == []
    assert red_pixels(image_of(rig, out["replacement"]))[2] > 3000


def test_a_denied_capture_releases_the_text_without_the_image(site: Site, rig: Rig, studio: App) -> None:
    text = f"Otworzyć {site.base}/index.html i kliknąć „Zapisz”."
    sent = site.writes["POST /api/save"]
    status, body = propose(studio, rig, text)
    assert status == 202, body
    rid = body["held"]["requests"][0]["id"]
    assert studio.call("POST", f"/api/captures/{rid}/deny", {})[0] == 200
    out = released(studio, rig)
    assert out["replacement"] == text and out["dropped"] == [{"key": "shot-1", "reason": "denied"}]
    assert rig.store.list_assets(rig.draft) == [] and site.writes["POST /api/save"] == sent
