#!/bin/bash
# Build the Astro pages and stamp them with the git build id (short hash, plus
# -dirty for uncommitted changes). The server injects the id into every page and
# serves it at /version; open pages reload on change.
# Into web/dist, or DS_OUT (deploy.sh stages there); DS_BUILD overrides the id.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO
readonly OUT="${DS_OUT:-$REPO/web/dist}"

main() {
    local build="${DS_BUILD:-}"
    if [[ -z "$build" ]]; then
        build="$(git -C "$REPO" rev-parse --short HEAD)"
        if [[ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]]; then
            build="${build}-dirty"
        fi
    fi
    (cd "$REPO/web" && pnpm install --frozen-lockfile && pnpm exec astro check && pnpm exec astro build --outDir "$OUT")
    printf '%s\n' "$build" > "$OUT/build-id"
    echo "built $build"
}

main "$@"
