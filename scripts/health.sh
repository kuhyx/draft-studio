#!/bin/bash
# Health check: the port serves the wanted build id and the API answers.
# Usage: health.sh [port]   Exit 0 healthy, 1 not.
# The wanted build is DS_WANT, else the id stamped in ${DS_WEB:-<this checkout>/web}/dist.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO
readonly PORT="${1:-8794}"
readonly WEB="${DS_WEB:-$REPO/web}"

main() {
    local want got=""
    want="${DS_WANT:-$(cat "$WEB/dist/build-id")}"
    for _ in $(seq 1 100); do
        got="$(curl -fsS --max-time 2 "http://127.0.0.1:$PORT/version" 2> /dev/null || true)"
        [[ "$got" == *"\"$want\""* ]] && break
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
