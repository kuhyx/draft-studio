#!/bin/bash
# Deploy at any time: pull, build into a staging dir, swap web/dist atomically, restart the unit
# at once, verify /version serves the new build. Nothing in flight is lost: Claude calls run in
# their own units and the new server replays the jobs it finds (draft_studio/jobs.py).
# Targets another instance through env (the restart test): DS_REPO (clone), DS_UNIT, DS_PORT,
# DS_WEB (dir holding dist), DS_BUILD (build id), DS_DIST_SRC (prebuilt dist: copied, not built),
# DS_NO_PULL=1 (no git).
set -euo pipefail

readonly REPO="${DS_REPO:-$HOME/src/draft-studio}"
readonly REMOTE="https://github.com/kuhyx/draft-studio.git"
readonly UNIT="${DS_UNIT:-draft-studio.service}"
readonly PORT="${DS_PORT:-8794}"
readonly WEB="${DS_WEB:-$REPO/web}"
readonly KEEP=3 # staged builds kept (the live one, the last, one spare)

pull() {
    if [[ ! -f "$REPO/pyproject.toml" ]]; then
        if [[ -d "$REPO/.git" ]]; then
            git -C "$REPO" fetch -q origin main
            git -C "$REPO" checkout -q -B main origin/main
        else
            git clone -q "$REMOTE" "$REPO"
        fi
    fi
    git -C "$REPO" pull -q --ff-only
}

build_id() {
    local build
    build="$(git -C "$REPO" rev-parse --short HEAD)"
    if [[ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]]; then
        build="${build}-dirty"
    fi
    echo "$build"
}

# Build into web/dist.<build>: the live server keeps serving web/dist untouched.
stage() {
    local build="$1" out="$WEB/dist.$1"
    rm -rf "$out"
    if [[ -n "${DS_DIST_SRC:-}" ]]; then
        cp -a "$DS_DIST_SRC" "$out"
        printf '%s\n' "$build" > "$out/build-id"
    else
        DS_OUT="$out" DS_BUILD="$build" bash "$REPO/scripts/build.sh"
    fi
}

# web/dist is a symlink to the live build: renaming a new link over it is one atomic step.
swap() {
    ln -sfn "dist.$1" "$WEB/dist.next"
    if [[ -d "$WEB/dist" && ! -L "$WEB/dist" ]]; then # first deploy: the old real dir steps aside
        rm -rf "$WEB/dist.legacy"
        mv "$WEB/dist" "$WEB/dist.legacy"
    fi
    mv -T "$WEB/dist.next" "$WEB/dist"
}

prune() {
    local old
    while IFS= read -r old; do
        rm -rf "${WEB:?}/$old"
    done < <(cd "$WEB" && ls -dt dist.* 2> /dev/null | tail -n +$((KEEP + 1)))
}

install_unit() {
    [[ "$UNIT" == "draft-studio.service" ]] || return 0
    mkdir -p "$HOME/.config/systemd/user"
    sed "s#%h/src/draft-studio#$REPO#" "$REPO/systemd/$UNIT" > "$HOME/.config/systemd/user/$UNIT"
    systemctl --user daemon-reload
}

main() {
    local build
    [[ "${DS_NO_PULL:-}" == 1 ]] || pull
    build="${DS_BUILD:-$(build_id)}"
    stage "$build"
    swap "$build"
    prune
    install_unit
    systemctl --user restart "$UNIT"
    DS_WANT="$build" DS_WEB="$WEB" bash "$REPO/scripts/health.sh" "$PORT"
    echo "deployed $build: restarted now; jobs in flight resume by themselves"
}

main "$@"
