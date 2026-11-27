"""The one OPTIONS request a capture lets through: a CORS preflight for a read.

kuhy's decision, 2026-11-27: a single-page app on one origin (panel.cyberfolks.pl)
reads its data from another (api.cyberfolks.pl). The browser first sends an
OPTIONS preflight with `Access-Control-Request-Method: GET`; refused, no data
call ever goes out and the shot shows a broken page. The preflight carries no
data and changes nothing. Any other OPTIONS (no such header, or a write
announced) is refused like every non-GET, and so is the write itself.
"""

from __future__ import annotations

from typing import Any

READS = ("GET", "HEAD")
HEADER = "access-control-request-method"


def announced(headers: dict[str, Any]) -> str | None:
    """The method an Access-Control-Request-Method header (name in any case) announces, upper-cased."""
    value = next((v for k, v in headers.items() if str(k).lower() == HEADER), "")
    return str(value).strip().upper() or None


def is_read_preflight(method: str, headers: dict[str, Any]) -> bool:
    """True for OPTIONS whose Access-Control-Request-Method (name in any case) is GET or HEAD."""
    return method == "OPTIONS" and announced(headers) in READS
