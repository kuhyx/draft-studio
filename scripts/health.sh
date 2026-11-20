#!/bin/bash
# Health check: the live port serves this checkout's build id and the API answers.
# Usage: health.sh [port]   Exit 0 healthy, 1 not.
set -euo pipefail

readonly REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PORT="${1:-8794}"

main() {
    local want got
    want="$(cat "$REPO/web/dist/build-id")"
    for _ in $(seq 1 50); do
        got="$(curl -fsS --max-time 2 "http://127.0.0.1:$PORT/version" 2> /dev/null || true)"
        [[ -n "$got" ]] && break
        sleep 0.2
    done
    if [[ "$got" != *"\"$want\""* ]]; then
        echo "health: want build $want, got: ${got:-nothing}" >&2
        exit 1
    fi
    curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/drafts" > /dev/null
    curl -fsS --max-time 2 "http://127.0.0.1:$PORT/" | grep -q "$want"
    echo "healthy: build $want on 127.0.0.1:$PORT"
}

main "$@"
