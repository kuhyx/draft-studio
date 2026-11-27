"""A server with durable jobs and a fake browser that can be cut mid-run, and its restart."""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from draft_studio import server as srv
from draft_studio.capture.service import CaptureService
from draft_studio.claude_fake import FakeClaude
from draft_studio.gmail import FakeGmail
from draft_studio.jobs import Jobs
from draft_studio.store import Store
from tests.conftest import App
from tests.test_capture_service import ShotPage
from tests.test_held import EFFECTS


class CutLauncher:
    """A fake browser; run number `cut_at` dies like a killed server (SystemExit), the others shoot."""

    def __init__(self, cut_at: int = 0) -> None:
        self.runs, self.cut_at = 0, cut_at

    def busy(self) -> bool:
        return False

    @contextlib.contextmanager
    def open(self) -> Iterator[Any]:
        self.runs += 1
        if self.runs == self.cut_at:
            raise SystemExit
        yield ShotPage(["Zapisz"], EFFECTS)


class Rig:
    """One data dir, booted again and again like the live service is after a deploy."""

    def __init__(self, tmp: Path) -> None:
        self.tmp, self.apps = tmp, []

    @property
    def data(self) -> Path:
        return self.tmp / "data"

    def boot(self, launcher: Any) -> App:
        dist = self.tmp / "dist"
        dist.mkdir(exist_ok=True)
        (dist / "index.html").write_text("x")
        store = Store(self.data)
        gmail = FakeGmail(self.data / "gmail-fake.json")
        jobs = Jobs(self.data)
        deps = srv.Deps(store, FakeClaude(), gmail, dist, self.tmp, "b1", jobs=jobs)
        httpd = srv.make_server(deps, port=0)
        httpd.captures = CaptureService(store, launcher)
        threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.02), daemon=True).start()
        app = App(f"http://127.0.0.1:{httpd.server_address[1]}", store, gmail, httpd)
        self.apps.append(app)
        return app

    def restart(self, app: App, launcher: Any) -> App:
        """Stop `app` and start the next server on the same data; it takes up what was left."""
        app.httpd.shutdown()
        app.httpd.server_close()
        fresh = self.boot(launcher)
        for thread in fresh.httpd.resume_jobs():
            thread.join(10)
        return fresh

    def jobs(self, op: str) -> list[dict[str, Any]]:
        found = [json.loads(p.read_text()) for p in sorted((self.data / "jobs").glob("*/job.json"))]
        return [j for j in found if j["op"] == op]

    def close(self) -> None:
        for app in self.apps:
            with contextlib.suppress(Exception):
                app.httpd.shutdown()
                app.httpd.server_close()
