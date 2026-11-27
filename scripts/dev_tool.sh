#!/bin/bash
# Run a pinned dev tool (ruff, mypy) for a pre-commit hook, in any checkout.
# Usage: scripts/dev_tool.sh <tool> [args...]
#
# A fresh `git worktree` has no .venv, so a hook entry of `.venv/bin/ruff`
# dies there. Resolution order, first hit wins:
#   1. this checkout's .venv/bin/<tool>
#   2. the main checkout's .venv/bin/<tool> (found via --git-common-dir)
#   3. <tool> on PATH
# The tool's version must equal the pin in pyproject.toml ([dev] extra), since
# lint and format results differ between versions. Missing tool or wrong
# version is a loud failure with the cause named, never a skip.
set -euo pipefail

die() {
    echo "dev_tool: $*" >&2
    exit 1
}

pinned_version() {
    local tool="$1" top pin
    top="$(git rev-parse --show-toplevel)"
    pin="$(grep -oE "\"$tool==[0-9][^\"]*\"" "$top/pyproject.toml" | head -1 | sed -E 's/.*==([^"]+)"/\1/')"
    [[ -n "$pin" ]] || die "no '$tool==<version>' pin in pyproject.toml"
    echo "$pin"
}

find_tool() {
    local tool="$1" top common main_root cand
    top="$(git rev-parse --show-toplevel)"
    common="$(cd "$top" && cd "$(git rev-parse --git-common-dir)" && pwd)"
    main_root="$(dirname "$common")"
    for cand in "$top/.venv/bin/$tool" "$main_root/.venv/bin/$tool"; do
        if [[ -x "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done
    command -v "$tool"
}

main() {
    local tool="${1:-}"
    [[ -n "$tool" ]] || die "usage: dev_tool.sh <tool> [args...]"
    shift
    local want bin have
    want="$(pinned_version "$tool")"
    bin="$(find_tool "$tool")" || die "$tool not found: no .venv/bin/$tool here or in the main checkout, none on PATH (install: uv tool install $tool==$want)"
    have="$("$bin" --version | awk '{print $2}')"
    [[ "$have" == "$want" ]] || die "$tool $have at $bin, pyproject.toml pins $want (fix: uv tool install --force $tool==$want, or pipx install --force $tool==$want)"
    exec "$bin" "$@"
}

main "$@"
exit $?
