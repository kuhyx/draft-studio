"""`python -m draft_studio`: build the server from the environment, take up the last server's work, serve.

The last server's work is its running jobs, the held answers they left stuck (heldsweep.py) and its
login window (loginwin.py). A stop (SIGTERM
from systemd on a restart or deploy) ends the accept loop, lets any job that is in the middle of
writing its answer finish (jobs.py `drain`), and exits. Work that waits on Claude needs no wait:
it runs in its own unit and the next server replays the job (durable.py).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from draft_studio.claude_fake import make_claude
from draft_studio.detach import systemd_spawn
from draft_studio.durable import DurableClaude
from draft_studio.gmail import make_gmail
from draft_studio.jobs import Jobs
from draft_studio.server import DEFAULT_PORT, REPO, Deps, make_server
from draft_studio.store import Store, default_data_dir

DRAIN_S = 15.0


def serve(server: Any, jobs: Jobs | None, install: Callable[[int, Any], Any] = signal.signal) -> None:
    """Serve until SIGTERM, then finish what is being written."""

    def stop(signum: int, frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    install(signal.SIGTERM, stop)
    server.serve_forever()
    if jobs is not None:
        jobs.drain(DRAIN_S)


def main(argv: list[str] | None = None) -> None:
    """Entry point: `python -m draft_studio [--port N] [--build ID] [--dist DIR]`.

    DRAFT_STUDIO_JOBS=inline runs Claude in this process (tests, the E2E harness): no job files.
    DRAFT_STUDIO_JOBS=files keeps the job files (grant, deny, retry resume after a restart) but
    still runs Claude in this process: the E2E restart tests have no systemd.
    """
    parser = argparse.ArgumentParser(prog="draft_studio")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--build", default="")
    parser.add_argument("--dist", type=Path, default=REPO / "web" / "dist")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    data = default_data_dir()
    build_file = args.dist / "build-id"
    build = args.build or (build_file.read_text().strip() if build_file.is_file() else "dev")
    claude, jobs = make_claude(), None
    mode = os.environ.get("DRAFT_STUDIO_JOBS")
    if mode != "inline":
        jobs = Jobs(data)
        if mode != "files":
            claude = DurableClaude(claude, jobs, systemd_spawn)
    deps = Deps(
        store=Store(data),
        claude=claude,
        gmail=make_gmail(data),
        dist=args.dist,
        locales=Path(os.environ.get("DRAFT_STUDIO_LOCALES") or REPO / "locales"),
        build=build,
        jobs=jobs,
    )
    server = make_server(deps, args.port)
    server.resume_jobs()
    server.resume_held()
    server.resume_login()
    serve(server, jobs)
