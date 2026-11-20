#!/bin/bash
# Build the Astro pages into web/dist and stamp them with the git build id
# (short hash, plus -dirty for uncommitted changes). The server injects the
# id into every page and serves it at /version; open pages reload on change.
set -euo pipefail

readonly REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

main() {
    local build
    build="$(git -C "$REPO" rev-parse --short HEAD)"
    if [[ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]]; then
        build="${build}-dirty"
    fi
    (cd "$REPO/web" && pnpm install --frozen-lockfile && pnpm build)
    printf '%s\n' "$build" > "$REPO/web/dist/build-id"
    echo "built $build"
}

main "$@"
