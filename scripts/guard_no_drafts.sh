#!/bin/bash
# Refuse to commit user data (drafts, history, Gmail state, tokens) to this
# public repo. .gitignore stops a plain `git add`; this pre-commit guard also
# stops `git add -f`. Default: check the staged paths. `--paths P...` checks
# the given paths instead (used by the tests on every tracked file).
# Exit: 0 clean, 1 a user-data path is staged.
set -euo pipefail

readonly DATA_RE='(^|/)(drafts|trash|data)/|(^|/)(draft\.md|meta\.json|history\.jsonl|gmail\.json|gmail-[^/]*\.json)$|\.draft\.md$|token[^/]*\.(json|txt)$|\.token$|(^|/)\.env'

main() {
    local -a paths
    if [[ "${1:-}" == "--paths" ]]; then
        shift
        paths=("$@")
    else
        mapfile -t paths < <(git diff --cached --name-only --diff-filter=ACMR)
    fi
    local bad=0 path
    for path in "${paths[@]}"; do
        if [[ "$path" =~ $DATA_RE ]]; then
            echo "guard_no_drafts: user data must not be committed: $path" >&2
            bad=1
        fi
    done
    exit "$bad"
}

main "$@"
