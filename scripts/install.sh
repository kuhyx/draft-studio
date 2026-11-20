#!/bin/bash
# Install draft studio as a systemd --user service on 127.0.0.1:8794 from the
# clone this script lives in (expected: ~/src/draft-studio). Idempotent.
# Needs: python3 (stdlib only at runtime), node + pnpm (build), and the
# claude CLI at ~/.local/bin/claude logged in to kuhy's subscription.
set -euo pipefail

readonly REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly UNIT="draft-studio.service"
readonly PORT=8794

need() {
    command -v "$1" > /dev/null || { echo "install: missing $1" >&2; exit 1; }
}

ensure_tools() {
    need python3
    need node
    if ! command -v pnpm > /dev/null; then
        corepack enable pnpm 2> /dev/null || npm install -g pnpm
    fi
    [[ -x "$HOME/.local/bin/claude" ]] || echo "install: warning: $HOME/.local/bin/claude missing; Claude actions will show the error icon" >&2
}

main() {
    ensure_tools
    bash "$REPO/scripts/build.sh"
    mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/share/draft-studio"
    chmod 700 "$HOME/.local/share/draft-studio"
    sed "s#%h/src/draft-studio#$REPO#" "$REPO/systemd/$UNIT" > "$HOME/.config/systemd/user/$UNIT"
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT" > /dev/null
    systemctl --user restart "$UNIT"
    bash "$REPO/scripts/health.sh" "$PORT"
}

main "$@"
