# draft-studio

Loopback web editor where kuhy and Claude write text drafts together.
Python stdlib server (`draft_studio/`) serves the Astro + CodeMirror 6 pages
(`web/`) on 127.0.0.1:8794 and runs `claude -p` headless on kuhy's
subscription (never the API, never `--bare`).

## Rules

- User data never enters this public repo. Drafts, history, Gmail state live in
  `~/.local/share/draft-studio` (`$DRAFT_STUDIO_DATA` in tests).
  `scripts/guard_no_drafts.sh` (pre-commit) blocks even `git add -f`.
- Ownership: Claude only proposes replacements for a selected span, shown as a
  word diff; nothing changes until kuhy accepts. kuhy's edits always win; an
  edit inside a pending span drops the proposal (stale), no merge.
- Visible chrome is icon-only; screen-reader labels and errors live in
  `locales/<tag>.json` (one file per language, may set `"dir": "rtl"`).
- Gmail: only draft tools are allowed (`draft_studio/gmail.py`); never send.

## Commands

- run: `DRAFT_STUDIO_CLAUDE=fake DRAFT_STUDIO_GMAIL=fake python3 -m draft_studio --port 8795` (live service: `systemctl --user status draft-studio`, http://127.0.0.1:8794/)
- install deps: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && (cd web && pnpm install)`
- test: `.venv/bin/pytest -q --cov && (cd web && pnpm coverage)`
- test-changed: `scripts/test_changed.sh`
- e2e: `bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e` (fake Claude + fake Gmail, headless chromium)
- live smoke (real claude -p): `python3 scripts/live_smoke.py`
- lint: `.venv/bin/ruff check . && .venv/bin/mypy && (cd web && pnpm lint)`
- build: `bash scripts/build.sh`
- coverage: `.venv/bin/pytest -q --cov && (cd web && pnpm coverage)`
- coverage-gaps: `coverage-gaps coverage.xml`
- deploy: `git -C ~/src/draft-studio pull --ff-only && bash ~/src/draft-studio/scripts/deploy.sh`
