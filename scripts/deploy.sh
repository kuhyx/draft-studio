#!/bin/bash
# Deploy: fast-forward the live clone ~/src/draft-studio (clone it if absent),
# rebuild, (re)install the user unit, and verify the live build id.
set -euo pipefail

readonly LIVE="$HOME/src/draft-studio"
readonly REMOTE="https://github.com/kuhyx/draft-studio.git"

main() {
    if [[ ! -f "$LIVE/pyproject.toml" ]]; then
        if [[ -d "$LIVE/.git" ]]; then
            git -C "$LIVE" fetch -q origin main
            git -C "$LIVE" checkout -q -B main origin/main
        else
            git clone -q "$REMOTE" "$LIVE"
        fi
    fi
    git -C "$LIVE" pull -q --ff-only
    bash "$LIVE/scripts/install.sh"
}

main "$@"
