"""What a new server takes up from the last one: its running jobs and its login window."""

from __future__ import annotations

import threading

from draft_studio import jobapi
from draft_studio.api import Api, Deps
from draft_studio.capture.service import CaptureService
from draft_studio.heldapi import HeldApi
from draft_studio.jobs import Job
from draft_studio.loginwin import LoginWindows


class Resumable:
    """The server's side of a restart (mixed into the HTTP server, server.py)."""

    deps: Deps
    captures: CaptureService
    logins: LoginWindows

    def resume_jobs(self) -> list[threading.Thread]:
        """Take up the jobs the last server left running (jobs.py); call once, before serving."""
        if self.deps.jobs is None:
            return []
        return self.deps.jobs.resume(self.replay)

    def replay(self, job: Job) -> object:
        """Run one job again from its file."""
        return jobapi.replay(Api(self.deps, self.captures), job)

    def resume_login(self) -> threading.Thread | None:
        """Adopt the login window the last server opened (loginwin.py); call once, before serving."""
        return self.logins.adopt()

    def retry_held(self, draft_id: str, hold: str) -> None:
        """Re-run the login/busy captures of a hold (the end of its login window)."""
        HeldApi(Api(self.deps, self.captures).shots()).retry(draft_id, hold)
