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
  edit inside a pending span drops the proposal (stale), no merge. Many
  proposals may be pending (spans never overlap; an answer overlapping another
  one is dropped). A request on a selection touching pending proposals revises
  them: the span widens to the union and Claude gets its old replacement
  (`previous` in `/propose`); a revision of a proposal kuhy rejected is dropped.
  No silent drops: both drops pulse a cross on the span (`web/src/lib/signals.ts`).
- No fake images (kuhy: "fake images are worse than no image at all"): `api.py`
  strips from Claude's answer every image link the draft does not hold
  (`draft_studio/links.py` `strip_invented`), keeps the rest of the text, logs
  the cut links (`stripped`), and the span pulses an image-off glyph. Links
  already in kuhy's span, `previous`, brief or answers stay (remote ones too);
  Claude may only add links to stored assets. Claude's own screenshots (`captures` in
  its answer, `draft_studio/shots.py`) are taken by the server before kuhy sees
  the answer: only URLs written in kuhy's text, a failed one drops its line,
  one waiting on a permission or login holds the answer (`held.py`, shapes in
  `draft_studio/capture/__init__.py`); the editor keeps its span tracked, polls
  (`web/src/lib/held.ts`, `editorHeld.ts`) and lands the release once; kuhy allows, denies, logs in
  or retries from the permission card (`editorCard.ts`, `permCard.ts`, `heldSpans.ts`; E2E
  `tests/e2e/test_permcard*.py`, fake login window). A link the stripper cannot cut
  drops the whole answer. kuhy may save any link, but approval (and so Gmail
  and PDF) refuses a missing asset with `missing-asset`, and the editor draws a red frame.
- Visible chrome is icon-only; screen-reader labels and errors live in
  `locales/<tag>.json` (one file per language, may set `"dir": "rtl"`).
  Exception kuhy asked for (2026-11-20): the `<ds-tip>` bubble
  (`web/src/lib/tooltip.ts`) shows a short `tips` word on hover, focus and
  long-press, plus a demo for ask/rewrite/change/accept/reject. Every locale
  keeps the same `tips` keys (`tests/test_locales.py`); do not strip them.
- Gmail: only draft tools are allowed (`draft_studio/gmail.py`); never send.
- Screenshot capture (`draft_studio/capture/`) only reads pages: clicks named
  elements, never types or submits, fails every non-GET before it is sent.
  Each refusal is a permission request kuhy grants or denies; never widen
  `policy.py` or the guard in `page.py` to skip asking.

## Commands

- run: `DRAFT_STUDIO_CLAUDE=fake DRAFT_STUDIO_GMAIL=fake python3 -m draft_studio --port 8795` (live service: `systemctl --user status draft-studio`, http://127.0.0.1:8794/)
- install deps: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && (cd web && pnpm install)`
- test: `.venv/bin/pytest -q --cov && (cd web && pnpm coverage)`
- test-changed: `scripts/test_changed.sh`
- e2e: `bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e` (fake Claude + fake Gmail, headless chromium)
- capture-live: `.venv/bin/pytest -q -m chromium -n 4 tests/capture_live` (screenshot capture on real headless chromium against a loopback test site; API in `draft_studio/capture/__init__.py`; `-n 4` overlaps the quiet-period waits: ~20 s instead of ~46 s)
- capture login (kuhy, once per site): `python3 -m draft_studio.capture login <url>`
- live smoke (real claude -p): `python3 scripts/live_smoke.py`
- live shots check (real claude -p asks one framed screenshot per guide step, then headless chromium takes them; run `ask` 2+ times, LLM variance): `.venv/bin/python scripts/live_shots_check.py ask <out.json> && .venv/bin/python scripts/live_shots_check.py shoot <out.json>`
- live Gmail check (real connector, scratch server per script header): `python3 scripts/gmail_live_check.py setup --to <own address>`, then `press <id>` / `refused <id>`
- lint: `.venv/bin/ruff check . && .venv/bin/mypy && (cd web && pnpm lint)`
- build: `bash scripts/build.sh`
- coverage: `.venv/bin/pytest -q --cov && (cd web && pnpm coverage)`
- coverage-gaps: `coverage-gaps coverage.xml`
- deploy: `git -C ~/src/draft-studio pull --ff-only && bash ~/src/draft-studio/scripts/deploy.sh`
