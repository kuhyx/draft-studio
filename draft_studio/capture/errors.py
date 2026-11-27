"""Named capture failures. Each maps to one HTTP status and an `error` code plus extra fields."""

from __future__ import annotations

from typing import Any


class CaptureError(Exception):
    """Base: `status`, `code` and the extra JSON fields the response carries.

    `taken`: (shot name, PNG) of the shots the run took before it failed (runner.run_all); a
    walk keeps them (service.py stores them, shots.py lands them), only later shots are failed.
    """

    status = 500
    code = "capture"

    def __init__(self, **extra: Any) -> None:
        super().__init__(f"{self.code} {extra}" if extra else self.code)
        self.extra = extra
        self.taken: list[tuple[str, bytes]] = []


class NeedsPermission(CaptureError):
    """A step was refused; the service turns it into a pending permission request."""

    status, code = 409, "needs-permission"

    def __init__(
        self,
        step: int,
        reason: str,
        detail: str,
        blocked: list[tuple[str, str]],
        shot: bytes,
        *,
        announces: list[str | None] | None = None,
    ) -> None:
        super().__init__()
        self.step, self.reason, self.detail, self.blocked, self.shot = step, reason, detail, blocked, shot
        self.announces = announces if announces is not None else [None] * len(blocked)


class LoginRequired(CaptureError):
    """The page asks for a login; kuhy runs `python3 -m draft_studio.capture login <url>`."""

    status, code = 409, "login-required"

    def __init__(self, origin: str, url: str) -> None:
        super().__init__(origin=origin, url=url)


class ProfileBusy(CaptureError):
    """The capture profile is held by the login window or another capture."""

    status, code = 409, "profile-busy"


class NoTarget(CaptureError):
    """No visible element shows the text (step None = the highlight)."""

    status, code = 422, "no-target"

    def __init__(self, step: int | None, text: str, seen: list[str]) -> None:
        super().__init__(step=step, text=text, seen=seen)


class Covered(CaptureError):
    """The element is gone or another element (a banner, a modal) sits over its centre."""

    status, code = 422, "covered-target"

    def __init__(self, text: str) -> None:
        super().__init__(text=text)


class Ambiguous(CaptureError):
    """Several visible elements match; the spec must name a unique one (aim.py: `within`, `nth`).

    `seen`: what surrounds each match, in document order, so Claude can pick a scope or an ordinal.
    """

    status, code = 422, "ambiguous-target"

    def __init__(self, step: int | None, text: str, count: int, seen: list[str]) -> None:
        super().__init__(step=step, text=text, count=count, seen=seen)


class ChromiumFailed(CaptureError):
    """Chromium exited or broke the DevTools protocol."""

    status, code = 502, "chromium-failed"

    def __init__(self, detail: str) -> None:
        super().__init__(detail=detail)


class Unguarded(CaptureError):
    """A target (`kind`: page, iframe, worker...) refused request interception: it never runs or gets shot."""

    status, code = 502, "unguarded-target"

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(kind=kind, detail=detail)


class NoChromium(CaptureError):
    """No executable chromium at the configured path."""

    status, code = 503, "no-chromium"


class CaptureTimeout(CaptureError):
    """The whole capture ran past its deadline."""

    status, code = 504, "timeout"


class Unjournaled(CaptureError):
    """A granted write could not be journaled (spent.py): it was failed, never sent unjournaled."""

    status, code = 500, "unjournaled"

    def __init__(self, detail: str) -> None:
        super().__init__(detail=detail)
