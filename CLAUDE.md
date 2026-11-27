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
  Claude may only add links to stored assets. A proposal never loses an image of its span
  (`draft_studio/keepimg.py`: kept unless a new shot of that step exists or the instruction asks to
  remove images; kuhy 2026-11-27: the old shots vanished with the failed new ones). Claude's own screenshots (`captures` in
  its answer, `draft_studio/shots.py`) are taken by the server before kuhy sees
  the answer: only URLs written in kuhy's text, a failed one drops its line,
  one waiting on a permission or login holds the answer (`held.py`, shapes in
  `draft_studio/capture/__init__.py`); the editor keeps its span tracked, polls
  (`web/src/lib/held.ts`, `editorHeld.ts`) and lands the release once; kuhy allows, denies (the image still lands), logs in
  or retries from the permission card (`editorCard.ts`, `permCard.ts`, `heldSpans.ts`; E2E
  `tests/e2e/test_permcard*.py`, fake login window). A proposal none of whose screenshots could be taken is never released as text-only
  (kuhy 2026-11-27: "it suggested edits but the images stayed the same"): it stays held with
  `failed` (key + reason rows, image-off span glyph, retry and dismiss; `shotout.py` `failed`,
  E2E `tests/e2e/test_failed_hold.py`); some missing lands marked partial (`missing` on the proposal,
  image-off glyph at its end). A walk that fails at step N keeps the shots taken before N
  (kuhy 2026-11-27: the login-page shot was failed with the click after it): the error reply carries them
  (`shots`, `capture/service.py` `_taken`), `shots.py` `settle` keeps them done through Claude's fix runs
  and only the later slots fail; none taken still holds as failed (`tests/test_shots_keep.py`). A link the stripper cannot cut
  drops the whole answer. kuhy may save any link, but approval (and so Gmail
  and PDF) refuses a missing asset with `missing-asset`, and the editor draws a red frame.
- Many places, one view (kuhy, 2026-11-27: "it should show in all of them a consistent
  view"): the server's saved state is the truth. Every editor save carries `base` (the
  version its text grew from); an older base gets 409 with the current state and the tab
  rebases its unsaved edits on it (`web/src/lib/sync.ts`, CodeMirror ChangeSet mapping:
  both tabs' edits stay, your typing never takes Claude's tint) and saves again. A save
  without `base` (scripts, `post_draft.py`) stays last-write-wins. Every meta write bumps
  the change feed (`storebase._put_meta` -> `feed.py`, SSE `GET /api/events`); one tab per
  browser holds the stream (Web Lock) and passes ids on over a BroadcastChannel
  (`feed.ts`): a stream per tab would take all six HTTP/1.1 connections and starve
  autosave (E2E `tests/e2e/test_multitab.py`). A remote edit inside a pending proposal
  drops it like kuhy's own and pulses the cross (`editorRemote.ts`). Offline: a failed
  save keeps the edits in the tab and retries every 2 s through the same rebase.
  Proposals, held answers and permission cards are server state too (part 2): pending proposals live in
  `drafts/<id>/proposals.json` (`proposals.py`, UTF-16 spans mapped through every text save, an edit in a
  span drops it as `gone: stale`), come with the draft state (`proposals`, `gone`), and are accepted or
  rejected by `POST /api/drafts/<id>/proposals/<pid>/accept|reject` (`proposalapi.py`, `rev`-checked, 409 with
  the state); the answer lands server-side when released, so a held answer lands once whichever tab polls. The held view's `from`/`to` are mapped through every edit since the ask (`shotout.py` `moved_span`, same mapping as the landing), so a tab that did not ask or was reloaded draws the current span (`tests/test_held_span.py`).
  A held-record change bumps the feed (`held.py`), and a tab refreshes state and held list on every event
  (`editorPage.ts` `refresh`). E2E `tests/e2e/test_multitab_proposals.py` (two tabs, a real server restart).
  Unsaved edits (text, Claude ranges, to, subject) also live in IndexedDB (`web/src/lib/unsaved.ts`,
  `editorKeep.ts`; one record per draft and tab, written on every edit, dropped when the server
  acknowledges). A reopened draft takes the records of every tab that is gone (Web Lock
  `draft-studio-tab-<id>`), oldest first, and merges each under the page's text with the 409
  rebase; a save that was in flight when the tab died and landed is not replayed (`baseOf` checks
  the history). A service worker (`web/public/sw.js`, stamped with the build id by `files.py`,
  `worker.ts` reloads pages on a new one) keeps pages and static files, never `/api/`, so a
  reload with the server dead opens on the record's saved state. E2E `tests/e2e/test_unsaved.py`.
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
  `policy.py` or `guard.py` to skip asking. A click is settled only once every
  write chromium announced reached the guard, and the browser shuts down with
  the guard still answering (`cdp.drain`). One exception (kuhy,
  2026-11-27, panel.cyberfolks.pl calls api.cyberfolks.pl cross-origin): a CORS
  preflight announcing a read, i.e. OPTIONS with `Access-Control-Request-Method`
  GET or HEAD (any case), passes (`capture/preflight.py`). Any other OPTIONS and the
  write itself stay refused.
  Deny (kuhy, 2026-11-27): "keep that request blocked, nothing is sent, take the screenshot
  anyway", no longer "cancel the capture and drop the image". `/deny` re-runs the capture with a
  denial (`capture/spec.py` `Denial`, kept for the whole run): the denied request is failed again
  and no longer stops it; a denied click ends the capture there, shot before the click with the
  target outlined. Nothing non-GET is sent without a grant, `policy.py` is unchanged. Details in
  `capture/__init__.py`.
  Walkthrough (kuhy, 2026-11-27: "the tool does it, not me"): one spec may hold `shot` and `goto`
  steps, so one browser run takes an answer's ordered shots (setup steps before the first shot
  are never shot) and a permission such as the "Zaloguj jako" POST is granted once for all of
  them. A `fresh` shot runs logged out in a new incognito context (`capture/fresh.py`), so a login
  page can be shot without logging kuhy out. Targets are exact aims (`capture/aim.py`): text or
  icon-only `role: "button"`, scoped by `within` and `nth`, never fuzzy. "Zakończ udostępnianie"
  / "end sharing" are deny-list stems (they end the cyber_Folks account share).
  Mimic site + E2E: `tests/capture_live/test_walk_live.py`.
  Grant reuse (kuhy, 2026-11-27): a granted request is reused by every re-run of the same walk
  for 10 minutes (after a later card is granted or denied, and Claude's fix runs): the same
  method + URL, sent by a click, same draft (`capture/reuse.py`, `tests/test_capture_reuse.py`).
  Denial reuse (kuhy, 2026-11-27: a fix run that `follows` carried no denials, so the denied robofolks
  chat POST carded in every fix run): a denied (method, URL) pair, from a deny or the rest of a partial
  grant, is kept the same way (same walk, draft, 10 min) and failed again with no card, shot still taken;
  it only blocks, `policy.py`/`guard.py` unchanged. A denied click is not kept; a later grant lifts it
  (`capture/reuse.py`, `tests/test_capture_denial_reuse.py`). Other walks or drafts, and expired grants, still card.
  Partial grant (kuhy, 2026-11-27): `POST /api/captures/<id>/grant {"allow": [[method, url], ...]}` sends only those blocked
  pairs (400 `not-blocked` otherwise), the rest stay denied, preflights follow their write (`capture/partial.py`,
  `tests/test_capture_partial.py`, `tests/capture_live/test_partial_live.py`). Card UI (kuhy approved the mockup,
  2026-11-27): one row per blocked pair with deny and allow, deny-rest/allow-rest decide only undecided rows, send stays
  off until every row is decided (no silent default) and posts exactly the allowed pairs (all denied: `/deny`); an
  OPTIONS row whose `announces` names a write shows as that write with a preflight mark but posts the OPTIONS pair
  (`web/src/lib/grantPick.ts`, `grantRows.ts`, `editorPicks.ts`; E2E `tests/e2e/test_grant_rows.py`). A click refusal
  (no `blocked`) keeps its immediate deny/allow; the large shot view of a card with rows has only close (an allow there
  would undo a row's deny).
  Claude's own walkthroughs: captures sharing a `walk` id run as one spec (`draft_studio/walks.py`,
  aims normalised from schema sentinels in `shotaim.py`); kuhy's instruction counts as his text
  for allowed URLs. Tests: `tests/test_shots_walk.py`; editor E2E `tests/e2e/test_walk_card.py`
  (fake Claude `[walk]`, one allow on the card lands every shot).
  A shot's highlight and the click right after it on the same text (or icon role in the same `within`)
  aim at one element (`walks.py` `align`, draft 462fb46c512a: two fix calls picked „Domeny” nth 1 and nth 2):
  on build the click takes the highlight's aim, on a merged fix the fixed capture's side wins (`tests/test_walk_align.py`).
- Login window (kuhy, 2026-11-27: "I logged in and the loading still shows"): nobody closes it by hand.
  `capture/loginwatch.py` closes it over DevTools once a login page was seen and every page is off it for
  2 s (the capture's own `policy.is_login_page`), or after 10 min; the hold's retry then runs. Cookies
  with no expiry die with chromium, so the window saves them to `capture-profile/session-cookies.json`
  (0600, 12 h, `capture/cookiejar.py`) and each headless run restores and refreshes them; never log values.
  A window that never shows a login page (the profile still holds the login, localStorage like the panel's
  `@react-set/auth/info` survives the clean close; or the form came and went between two polls) closes
  after 12 s on a loaded page of the origin (`UNSEEN_S`). Mimic SPA: `tests/capture_live/site/spa/`.
  Tests: `tests/test_loginwatch.py`, `tests/test_cookiejar.py`, `tests/capture_live/test_login_live.py`.
- Login page shots (kuhy, 2026-11-27: step 1 of his guide is "screenshot of .../security/login"): aiming at
  a login page is not a need to log in. A spec opening on a login URL (`policy.is_login_url`) runs its
  leading shots logged out in the fresh context before the session opens anything (`runner._login_shots`);
  a stepless session capture of a login URL in a walk is flagged fresh (`walks.py`); the prompt tells Claude
  to plan it `fresh`. A NON-login target landing on a login page is still login-required.
  Tests: `tests/test_capture_walk_runner.py`, `tests/test_walks_login.py`, `tests/capture_live/test_walk_live.py`.
- Already-switched state (kuhy, 2026-11-27: cyber_Folks keeps an impersonation for 1 h, so a re-run has no
  "ZALOGUJ JAKO"): a click with no target is skipped (logged) when its goal already holds, `capture/already.py`.
  Goal = the `wait` right after the click plus the next session shot's highlight; it holds when the wait text shows
  (`Page.seen`) and the highlight resolves on this page, else after a GET of the origin root (wait polled). Nothing
  to tell apart (no wait, no highlight) or not met: `no-target` as before. The planner puts a state-only `wait`
  after such clicks and never plans end-session clicks; a fix never retargets an action button to unrelated text
  (`prompts.py`). A skipped click leaves the walk where the click would have (kuhy, 2026-11-27, draft 462fb46c512a:
  the panel's side menu shows „Domeny” on every page, so the dashboard shot was taken on the shares page): a click
  names where it `lands` ("home" = origin root, or a URL from kuhy's text, which `Shot.urls` allow-lists), and a
  skipped one opens it and checks the goal only there; no `lands` (a banner that stays put, older plans) keeps the
  here-then-root check. Tests: `tests/test_capture_already.py`, `tests/test_capture_already_lands.py`,
  `tests/capture_live/test_already_live.py`, `tests/capture_live/test_already_lands_live.py` (`menu-shares.html`).

## Commands

- run: `DRAFT_STUDIO_CLAUDE=fake DRAFT_STUDIO_GMAIL=fake python3 -m draft_studio --port 8795` (live service: `systemctl --user status draft-studio`, http://127.0.0.1:8794/)
- install deps: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && (cd web && pnpm install)`
- test: `.venv/bin/pytest -q --cov && (cd web && pnpm coverage)`
- test-changed: `scripts/test_changed.sh`
- e2e: `bash scripts/build.sh && .venv/bin/pytest -q -m e2e tests/e2e` (fake Claude + fake Gmail, headless chromium; `-n 4` runs the whole dir in ~45 s: a module's tests are a script, so addopts `--dist loadfile` keeps each file on one worker and `tests/e2e/order.py` keeps source order)
- capture-live: `.venv/bin/pytest -q -m chromium -n 4 tests/capture_live` (screenshot capture on real headless chromium against a loopback test site; API in `draft_studio/capture/__init__.py`; `-n 4` overlaps the quiet-period waits: ~20 s instead of ~46 s)
- capture login (kuhy, once per site): `python3 -m draft_studio.capture login <url>`
- live smoke (real claude -p): `python3 scripts/live_smoke.py`
- live shots check (real claude -p, asked like kuhy, plans his cyber_Folks walkthrough; `shoot` runs it on headless chromium against the loopback mimic and grants the one impersonation; run `ask` 2+ times, LLM variance): `.venv/bin/python scripts/live_shots_check.py ask <out.json> && .venv/bin/python scripts/live_shots_check.py shoot <out.json>`
- live Gmail check (real connector, scratch server per script header): `python3 scripts/gmail_live_check.py setup --to <own address>`, then `press <id>` / `refused <id>`
- lint: `.venv/bin/ruff check . && .venv/bin/mypy && (cd web && pnpm lint)`
- build: `bash scripts/build.sh`
- coverage: `.venv/bin/pytest -q --cov && (cd web && pnpm coverage)`
- coverage-gaps: `coverage-gaps coverage.xml`
- deploy: `bash ~/src/draft-studio/scripts/deploy.sh` (pulls, builds into `web/dist.<build>`, swaps the `web/dist` symlink, restarts at once, checks `/version`; about 6 s with a warm pnpm store; safe at any time, see Deploy model)
- restart under load (real systemd --user, throwaway instance; one test per call, each under 60 s): `python3 -m pytest -q -m deploy tests/deploy_live/test_login_live.py` (also `test_restart_live.py`)

## Deploy model

- Live runs from: `~/src/draft-studio` (unit `draft-studio.service`, :8794), pages from the `web/dist` symlink (a staged `web/dist.<build>`), data in `~/.local/share/draft-studio`.
- `web/dist` is never missing: a normal run renames a new link over it; the first run on a clone where it is still a real directory trades dir and link in one step (`mv -T --exchange`) and parks the old dir as `dist.legacy`. Prune keeps the 2 newest builds (`dist.legacy` counts, oldest) plus the live one; redeploying the live build stages under a fresh name. Check: `tests/deploy_live/test_first_run_live.py` (a `mv`/`rm` shim logs whether `web/dist` resolves after each call).
- Deploy command: `bash ~/src/draft-studio/scripts/deploy.sh`. It restarts the unit at once and never waits for work in flight (kuhy, 2026-11-27: "deploy at ANY TIME and it does not break any work in progress").
- Restart effects: none lost. A request that waits on Claude (brief, intake, ask, propose) is a job file `<data>/jobs/<id>/job.json` (`draft_studio/jobs.py`); its `claude -p` runs in its own `systemd-run --user` unit (`detach.py`, `callrun.py`, `durable.py`), which a restart of the server unit does not stop (the draft-studio unit has KillMode control-group: `start_new_session` alone would die with it). The next server replays running jobs (`boot.py`, `resume_jobs`): finished calls come from the job's journal, unfinished ones are waited for, a dead one is started once more then marked failed (`GET /api/busy`, `POST /api/jobs/<id>/retry`). What lands carries the job id in the same atomic write (`Store.save(job=)`, a proposal's `proposals.json`, a held record), and `jobapi.replay` skips a job whose answer landed (`Store.landed`; `HeldStore.by_job` for a held one), so a brief, intake, ask or propose replay lands once (`tests/test_landing.py`); only the history line of a proposal can be lost, never doubled. SIGTERM waits up to 15 s for a job that is writing its answer. Permission cards and held answers are files: answerable after a restart (`tests/test_restart_cards.py`). Open tabs reconnect the SSE stream and refresh; a new build reloads a tab only after its edits are saved.
- The login window is a unit of its own (`loginwin.py`, `ds-login-<hold>-*`) with `<data>/login-window.json`; a new server adopts a live one, waits for it, runs the hold's retry once, and runs the retry of one that died meanwhile (`tests/deploy_live/test_login_live.py`). That retry is the held-retry job (op `retry`, body `{hold, window}`, `Resumable.retry_held`), the same one `POST /api/drafts/<id>/held {"retry": id}` runs for the card's retry button (no login window needed); the job writes its id into the record with its result, and an adopting server whose window already has a retry job (`Jobs.find`, running or done) only clears the window file: never two retries (`tests/test_held_retry.py`).
- Grant and deny are jobs too (`draft_studio/heldjobs.py`, ops `grant`/`deny`, body `{rid, body}`): the job file is written before the request is claimed and covers the capture re-run and the held slot's move. A restart in between leaves the job `running`; the next server replays it: nothing to do when the slot no longer waits on that request, else the same decision runs again from the stored record (`PendingStore.claim(resume=True)`), queued behind a running capture (`RESUME_WAIT_S`). A granted request a hold waits on is never pruned at boot (`HeldStore.waiting_requests`). One grant, one write (kuhy, 2026-11-27): the guard journals each granted (method, URL) into the request's record, fsync'd, right before it lets the write leave (`capture/spent.py`, `PendingStore.spend`; a record it cannot write fails the request, `unjournaled`). A replay drops the journaled pairs from every grant and from the walk's reuse, and fails them in that run only like a denial, no card, shots still taken; a walk that cannot go on without the write fails, and Claude's fix run cards it again for kuhy. Fail closed: a pair granted twice and sent once is not sent again. E2E `tests/e2e/test_restart_grant.py` (exactly one write) (`DRAFT_STUDIO_JOBS=files`: job files, Claude in-process), unit `tests/test_held_jobs.py`, `tests/test_held_once.py`.
- Never stuck (kuhy, 2026-11-27): at boot, after the job replays started, `Resumable.resume_held` runs `draft_studio/heldsweep.py` once: a held record no replayed job owns whose request was decided by a run nobody finished is asked again as a new capture (a fresh card, nothing sent), busy/new slots get the retry job, a fully resolved record is released. A grant, deny or retry that fails for good (replay or live) drops what it waited on as `failed` (`HeldApi.abandon`): the hold shows as failed with retry and dismiss. Tests `tests/test_held_sweep.py`.
- Retry without a login window (kuhy, 2026-11-27): a login row on the permission card also has an icon-only retry (`permCard.ts` `loginRow`, existing `retry` glyph and locale label) that calls the held retry route (`POST /api/drafts/<id>/held {"retry": id}`, a job); it re-runs the hold's login captures at once, so a login that already holds (cookies came late, window closed by hand) needs no window. Test `web/src/lib/permCard.test.ts`.
- Chromium capture is killed with the unit by design (it holds the only network guard); the cut run is the job's replay.
