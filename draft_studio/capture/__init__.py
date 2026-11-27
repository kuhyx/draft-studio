"""Screenshot capture: real screenshots of web pages for how-to drafts, never invented images.

Headless /usr/bin/chromium on its own persistent profile (<data dir>/capture-profile),
driven over DevTools on a port chromium picks (never 9222, never kuhy's display).
kuhy logs into a site once, himself, in a headed window on that profile:

    python3 -m draft_studio.capture login https://panel.example/

The window closes itself once the login is done (loginwatch.py: a login page was seen and every
page is off it, one of the site's origin loaded, for 2 s) or after 10 minutes; closing it by hand
works too. Chromium drops cookies that have no expiry when it exits, and a site's auth cookie is
often one: the window saves them to the profile's session-cookies.json (0600, cookiejar.py) while
open, and every headless run restores them before its first page and refreshes them on the way
out, for 12 hours after the login. A fresh (incognito) shot never gets them. A window that never
saw a login page (the profile still holds the login) closes after 12 s on a loaded page of the origin.

A capture only reads: it clicks elements named in the spec, never types, never
submits, and every non-GET request is failed before it is sent. Anything it
may not do becomes a permission request kuhy grants or denies.

One exception (kuhy's decision, 2026-11-27): a CORS preflight for a read passes,
i.e. an OPTIONS request whose `Access-Control-Request-Method` header (name in any
case) is GET or HEAD. Without it the data calls of a single-page app on another
origin (panel.cyberfolks.pl -> api.cyberfolks.pl) never go out and the shot shows
a broken page. An OPTIONS without that header, or announcing POST/PUT/PATCH/
DELETE, is refused as before; so is the write itself.

A click that opens a new tab (window.open, target=_blank) moves the capture to
that tab: later steps, the highlight and the screenshot run there, under the
same rules, and every other tab is closed (tabs.py). A click that was refused
stays on its own tab, so the refusal's screenshot outlines what was clicked.

HTTP API (JSON; errors are {"error": <code>, ...}):

POST /api/drafts/<id>/capture
    body {"url": "https://...", "name": "<slug a-z0-9->",
          "steps": [{"click": <aim>, "lands"?: "home" | "https://..."} | {"wait": "<visible text>"}
                    | {"goto": "https://..."} | {"shot": <shot>}],
          (`lands`: where the click leads; a click skipped as already in effect opens it, already.py)
          "highlight": <aim> | null}      null when the steps hold shots
    200 {"asset": "<name>-<sha12>.png", "md": "![](assets/<asset>)"}     no shot steps
    200 {"shots": [{"name": <shot name>, "asset", "md"}, ...]}          a walkthrough
    409 {"error": "needs-permission", "request": <request>}
    409 {"error": "login-required", "origin": "https://host", "url": <spec url>}
    409 {"error": "profile-busy"}            login window open, or a capture running
    400 {"error": "bad-spec", "detail": "url" | "name" | "steps" | "step-<n>" | "highlight" | "body"}
    422 {"error": "no-target", "step": n | null, "text": ..., "seen": [visible labels]}
    422 {"error": "ambiguous-target", "step": n | null, "text": ..., "count": n,
         "seen": [the text around each match: its group or row]}
         (step: the step index; a walkthrough shot's highlight names its shot step, the one
          highlight of a spec without shots stays null)
    422 {"error": "covered-target", "text": ...}
    502 {"error": "chromium-failed", "detail": ...}
    502 {"error": "unguarded-target", "kind": <target type>, "detail": ...}  no request interception
    503 {"error": "no-chromium"}
    504 {"error": "timeout"}                 $DRAFT_STUDIO_CAPTURE_TIMEOUT, default 45 s
    any error of a walkthrough except needs-permission also carries "shots": [<shot>, ...], the
         shots taken (and stored) before the failing step; absent when none were taken
    404 {"error": "not-found"}               no such draft
GET  /api/captures/pending      200 {"requests": [<request>, ...]} oldest first
GET  /api/captures/<rid>/shot   200 image/png: the screen with the refused target outlined
POST /api/captures/<rid>/grant  re-runs the capture with that step allowed, once;
                                same responses as POST .../capture; 409 not-pending
                                optional body {"allow": [["POST", "https://..."], ...]}: a partial grant
                                (partial.py), see below; no body or no `allow` grants the whole card
                                400 {"error": "not-blocked"}  a pair is not in the card's `blocked`
                                400 {"error": "bad-allow"}    `allow` is not a non-empty list of
                                                              [method, url] strings (both: still pending)
POST /api/captures/<rid>/deny   re-runs the capture with that refusal denied (see below):
                                same responses as POST .../capture; 409 not-pending, 404 not-found,
                                409 profile-busy (the request stays pending)

<request> = {"id": "<16 hex>", "draftId", "spec", "step": n, "stepText",
             "reason": "deny-list" | "submit" | "input" | "request" | "form-submit",
             "detail": "<stem>" | "<METHOD url>", "allow": <grant>, "grants": [<grant>],
             "denials": [<grant>, ...], "status": "pending" | "granted" | "denied", "created",
             "shot": "/api/captures/<rid>/shot", "blocked": [["POST", "https://..."], ...],
             "announces": ["POST" | null, ...]}  aligned with `blocked`: an OPTIONS preflight's
             Access-Control-Request-Method upper-cased (the write it announces), else null
<grant>   = {"step": n, "click": bool, "requests": [["POST", "https://..."], ...]}  (denials: same shape)

<aim>  = "<visible text>"                       text, aria-label, title, alt or button value, exact
       | {"text": "<visible text>" | "role": "button",  one of the two; role: icon-only buttons
          "within": "<visible text>",           optional: inside the one element showing it
          "nth": n}                             optional: 1 first, -1 last, in document order
       details in aim.py: never fuzzy; a miss or several matches is an error Claude corrects
<shot> = {"name": "<slug>", "highlight": <aim> | null,
          "fresh": true, "url": "https://..."}  fresh + url: logged out, see below

Walkthrough (kuhy, 2026-11-27: the tool reproduces his screenshot walkthrough by
itself): ONE browser run for an answer's ordered shots. Steps before the first
shot are setup, never shot (kuhy's own account, e.g. "Zaloguj jako" on the
shares page); each shot is taken from the state the steps before it left, with
its own highlight (red outline plus arrow, icon-only targets too). A `goto`
opens another URL of kuhy's. Because it is one run, a permission (the
impersonation POST) is asked once and granted once for every shot after it.
A denied click ends the walk: the shots so far, plus the next shot as the page
before the click with the target outlined.
A fresh shot opens its `url` in a new incognito browser context
(Target.createBrowserContext, fresh.py): no cookies, so a login page shows its
form, and the session stays logged in. Its tab is armed like any other (reads
only) and it skips the login check; it takes no clicks.
A spec that opens on a login URL (policy.is_login_url) and starts with shot steps aims at the login
page: those leading shots run as fresh shots (runner._login_shots, before the session tab opens
anything), so the page is shot instead of answered with login-required. Any other target that lands
on a login page is still login-required.

A grant re-runs the whole capture from the start. A click allowance is spent by
one run (see pending.py). A granted request is kept for its walk (kuhy,
2026-11-27): every re-run of the same walk within 10 minutes sends the same
(method, URL) again without a card, at any click step: the re-run after a later
card is granted or denied (a tracker POST after the impersonation,
tests/test_capture_walk_service.py) and Claude's fix runs after a 422. A walk is
the chain of requests one capture started, in one draft (reuse.py; the body is
not compared, why is there). Another request, walk or draft, a denial, or an
expired grant: a card as before. Tests: tests/test_capture_reuse.py.

Partial grant (kuhy, 2026-11-27: a card blocked the impersonation POST he wanted and a chat/tracking
POST he did not): `allow` picks some of the card's `blocked` pairs (method upper-cased, URL exact).
Those are granted, once, as before; the other `blocked` pairs become a denial (failed again, never
sent, no new card, the capture still goes on to its shot) kept for the chain's later re-runs like any
deny. The CORS preflight (OPTIONS, same URL) of a picked write is granted with it, listed or not, and
the preflight of a write left blocked is denied with it; a SUBMIT pair has none. Picking only a
preflight (the card lists OPTIONS when the preflight stopped the write) lets it go out, and the write
behind it is the next card. Grant reuse (10 min, same walk) keeps only the granted pairs. A fix run
that `follows` the request keeps the denied pair failed too (10 min, same walk): no card (reuse.py).
The card shows one row per blocked pair (`announces` names a preflight's write) and grants per row:
it posts exactly the allowed pairs as `allow`, or `/deny` when every row is denied (web/src/lib/grantRows.ts).

Deny (kuhy's decision, 2026-11-27): "keep that request blocked, nothing is sent,
take the screenshot anyway". It re-runs the capture with a denial, the mirror of
a grant. Before, a deny cancelled the capture and the answer lost its image
(a chat/tracking widget's background POST after clicking "Domeny" lost every shot).

- a refused request ("request", "form-submit"): the denied (method, url) pairs are
  failed again, never sent, and no longer stop the capture. It goes on to its
  screenshot, or to a refusal for something else (a new request).
- a refused click ("deny-list", "submit", "input": the step itself): a click cannot
  be continued past, since later steps depend on its result. The capture ends at
  that step: the shot is the page before the click with the target still outlined
  (the same picture kuhy saw on the card), the later steps and the spec's highlight
  are skipped. Nothing is clicked.
- a denial only blocks, so it is kept for the whole run of that capture
  (`denials`, carried by every later request of the chain, granted or denied);
  a click grant is spent by one run, a denial never is. Denied requests apply on every step.
- the re-run keeps a click kuhy had allowed before (the "Zapłać" case: click allowed,
  its POST denied); it never allows the denied refusal itself.

Claude's own screenshots (draft_studio/shots.py, held.py, heldapi.py)
-----------------------------------------------------------------------
Draft studio's Claude asks for screenshots in its answer (`captures`, see
draft_studio/prompts.py) and the server takes them BEFORE kuhy sees the
answer. A URL not written in kuhy's text is never opened. A 422 no-target or
ambiguous-target goes back to Claude with `seen`, at most twice; then, like any
other failure or an expiry (7 days, or the request pruned), the image
line is dropped and recorded as {"key", "reason"} in `dropped`.

Claude's walkthroughs (draft_studio/walks.py): captures sharing a `walk` id become ONE
capture spec in answer order (each capture's steps, then its shot step; `fresh` ones
open their own URL logged out), so the impersonation is asked once for the whole guide.
Every URL of a walk (start, each goto, each fresh shot) must be in kuhy's text or his
instruction for this ask; one bad URL or malformed capture drops the whole walk. A 200
`shots` reply fills the slots by name (a slot with no shot, e.g. after a denied click, is
dropped as "not-shot"); a 409 puts every slot of the walk on the same request id, so a
grant or deny moves them together; the `shots` of a failed run stay taken (a later run
replaces them, an error never drops them); a 422 `step` is mapped to the capture it belongs to
(its step index, null for its highlight) before Claude fixes that one capture, and the
whole walk runs again. A fix run stays in the walk (`follows` its request id), so it
reuses the walk's grants for 10 minutes (capture/reuse.py): no new impersonation card.

POST /api/drafts/<id>/propose   200 {"replacement", "from", "to", "stripped": [...], "dropped": [<drop>],
                                "partial": bool, ...}  partial: some screenshot is missing (the proposal
                                keeps `missing` = their keys); an answer none of whose screenshots was taken
                                is NOT released: 202 {"held": <held>} with `failed` (kuhy 2026-11-27)
POST /api/drafts/<id>/brief | /intake   (first draft) 200 {"questions": [], "state", "stripped", "dropped"}
    both: 202 {"held": <held>} while a capture waits on kuhy or the profile
GET  /api/drafts/<id>/held      200 {"held": [<held>], "released": [<released>]} oldest first
POST /api/drafts/<id>/held      {"retry": "<held id>"}: re-run its login/busy captures (after
                                `capture login`; a failed hold: every dropped one, from the start);
                                200 with the new listing, 404 no such held id
                                {"collect": ["<held id>", ...]}: forget released ones the client
                                showed, and failed holds (dismiss); 200 {"collected": n};
                                400 {"error": "bad-held"}
POST /api/drafts/<id>/held/<held id>/login   body {"origin": "https://host"} (one of <held>.login)
                                202 {"started": true}: `python3 -m draft_studio.capture login <origin>`
                                runs in its own systemd unit (a restart adopts it); when its window
                                closes (by itself after the login, at the 10 min timeout, or by hand)
                                the server retries that
                                hold by itself (client: keep polling GET .../held)
                                409 {"error": "profile-busy"}   login window open or profile held
                                400 {"error": "bad-origin"}     origin not in the hold's `login` list
                                404 {"error": "not-found"}      no such draft or held id
                                502 {"error": "login-failed"}   the login process did not start
                                (loginapi.py)

<held>     = {"id": "<16 hex>", "kind": "proposal" | "draft", "from": n | null, "to": n | null,
              (from/to: the span in the draft text as it is now, ask-time positions carried through every
              edit since; null for a first draft or once an edit touched the span: shotout.py `moved_span`)
              "requests": [<request>],  pending permission requests: grant/deny them as above
              "login": ["https://origin", ...],  log in, then POST .../held {"retry": id}
              "busy": bool,             profile was busy (login window open): retry
              "tries": n,               retries run so far (a login window closed without a login
                                        still bumps it, so the client sees the hold move)
              "failed": [<drop>]}       a proposal asking for screenshots of which none was taken
                                        (shotout.py `failed`): held, not released as text-only, with
                                        no request/login/busy; retry or dismiss it, it never expires
<released> = proposal: {"id", "kind", "replacement", "from", "to", "span", "previous",
                        "stripped", "dropped", "partial"}  apply only while the doc still holds `span`
             draft:    {"id", "kind", "questions": [], "state", "stripped", "dropped", "partial"}
                        (a first draft is never held for failed shots: there is no old image to lose)
                        already saved server-side (phase "editing")
<drop>     = {"key": "<capture key>", "reason": "expired" | "url" | "bad-spec" |
              "no-request" | "<capture error code>"}

A grant or deny (POST /api/captures/<rid>/grant|deny) answers as before and
also moves the held answer that waits on <rid>: a 409 needs-permission
re-links it to the new request, a 200 fills its image, and once no capture
waits the answer is released. A deny never drops the image line.

Old images (draft_studio/keepimg.py): an image line the span (or the revised replacement) held
and the answer lacks is put back, unless its new shot of the same step exists (the n-th lost
image pairs with the n-th marker of its changed hunk) or the instruction asks to remove images.
"""
