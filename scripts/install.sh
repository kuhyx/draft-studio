#!/bin/bash
# Install draft studio as a systemd --user service on 127.0.0.1:8794 from the
# clone this script lives in (expected: ~/src/draft-studio). Idempotent.
# Needs: python3 (stdlib only at runtime), node + pnpm (build), chromium at
# /usr/bin/chromium (PDF export; installed here if missing), and the
# claude CLI at ~/.local/bin/claude logged in to kuhy's subscription.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO
readonly UNIT="draft-studio.service"
readonly PORT=8794

need() {
    command -v "$1" > /dev/null || { echo "install: missing $1" >&2; exit 1; }
}

# PDF export prints with /usr/bin/chromium (draft_studio/export.py). Any package
# providing "chromium" counts (kuhy's box: ungoogled-chromium-bin).
ensure_chromium() {
    if ! pacman -Q chromium > /dev/null 2>&1; then
        sudo pacman -S --needed --noconfirm chromium
    fi
    [[ -x /usr/bin/chromium ]] || { echo "install: /usr/bin/chromium missing; PDF export will answer no-chromium" >&2; exit 1; }
}

ensure_tools() {
    need python3
    need node
    if ! command -v pnpm > /dev/null; then
        corepack enable pnpm 2> /dev/null || npm install -g pnpm
    fi
    ensure_chromium
    [[ -x "$HOME/.local/bin/claude" ]] || echo "install: warning: $HOME/.local/bin/claude missing; Claude actions will show the error icon" >&2
}

main() {
    # DRAFT_STUDIO_SKIP_BUILD=1 reuses an existing web/dist (sandbox tests).
    [[ "${DRAFT_STUDIO_SKIP_BUILD:-}" == 1 ]] || ensure_tools
    mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/share/draft-studio"
    chmod 700 "$HOME/.local/share/draft-studio"
    sed "s#%h/src/draft-studio#$REPO#" "$REPO/systemd/$UNIT" > "$HOME/.config/systemd/user/$UNIT"
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT" > /dev/null
    if [[ "${DRAFT_STUDIO_SKIP_BUILD:-}" == 1 ]]; then
        systemctl --user restart "$UNIT"
        bash "$REPO/scripts/health.sh" "$PORT"
    else # staged build, atomic swap, restart, verify (the same path as every later deploy)
        DS_REPO="$REPO" DS_NO_PULL=1 bash "$REPO/scripts/deploy.sh"
    fi
}

main "$@"
