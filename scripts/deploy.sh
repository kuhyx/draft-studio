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
readonly KEEP=2 # newest builds kept (dist.legacy counts as one); the live one is always kept too

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

# Build into a dir of its own: the live server keeps serving web/dist untouched. Prints the dir name.
# Redeploying the build that is live must not remove the dir being served, so it gets a unique name.
stage() {
    local build="$1" name="dist.$1" out
    [[ "$(readlink "$WEB/dist" 2> /dev/null)" != "$name" ]] || name="$name.$(date +%s)"
    out="$WEB/$name"
    rm -rf "$out"
    if [[ -n "${DS_DIST_SRC:-}" ]]; then
        cp -a "$DS_DIST_SRC" "$out"
        printf '%s\n' "$build" > "$out/build-id"
    else
        DS_OUT="$out" DS_BUILD="$build" bash "$REPO/scripts/build.sh" >&2
    fi
    touch "$out" # newest build by mtime, whatever the copied tree carried
    echo "$name"
}

# web/dist is a symlink to the live build, and it is never missing, not even for an instant.
# Normal run: a new link renamed over it is one atomic step. First run (web/dist a real dir, as
# the old build.sh left it): renameat2 RENAME_EXCHANGE (mv --exchange) trades the dir and the new
# link in one step, then the old dir is parked as dist.legacy (the oldest build, pruned first).
swap() {
    local name="$1"
    if [[ -d "$WEB/dist.next" && ! -L "$WEB/dist.next" ]]; then # a first run died between the steps
        rm -rf "$WEB/dist.legacy"
        mv "$WEB/dist.next" "$WEB/dist.legacy"
    fi
    ln -sfn "$name" "$WEB/dist.next"
    if [[ -d "$WEB/dist" && ! -L "$WEB/dist" ]]; then
        touch -d @1 "$WEB/dist"
        mv -T --exchange "$WEB/dist.next" "$WEB/dist"
        rm -rf "$WEB/dist.legacy"
        mv "$WEB/dist.next" "$WEB/dist.legacy"
    else
        mv -T "$WEB/dist.next" "$WEB/dist"
    fi
}

# Keep the KEEP newest builds (the live one is one of them, or kept on top); the rest goes.
prune() {
    local live name kept=0
    live="$(readlink "$WEB/dist")"
    while IFS= read -r name; do
        [[ "$name" != "dist.next" ]] || continue
        if ((kept < KEEP)) || [[ "$name" == "$live" ]]; then
            kept=$((kept + 1))
        else
            rm -rf "${WEB:?}/$name"
        fi
    done < <(cd "$WEB" && ls -dt dist.* 2> /dev/null)
}

install_unit() {
    [[ "$UNIT" == "draft-studio.service" ]] || return 0
    mkdir -p "$HOME/.config/systemd/user"
    sed "s#%h/src/draft-studio#$REPO#" "$REPO/systemd/$UNIT" > "$HOME/.config/systemd/user/$UNIT"
    systemctl --user daemon-reload
}

main() {
    local build name
    [[ "${DS_NO_PULL:-}" == 1 ]] || pull
    build="${DS_BUILD:-$(build_id)}"
    name="$(stage "$build")"
    swap "$name"
    prune
    install_unit
    systemctl --user restart "$UNIT"
    DS_WANT="$build" DS_WEB="$WEB" bash "$REPO/scripts/health.sh" "$PORT"
    echo "deployed $build: restarted now; jobs in flight resume by themselves"
}

main "$@"
