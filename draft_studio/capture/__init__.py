"""Screenshot capture: real screenshots of web pages for how-to drafts, never invented images.

Headless /usr/bin/chromium on its own persistent profile (<data dir>/capture-profile),
driven over DevTools on a port chromium picks (never 9222, never kuhy's display).
kuhy logs into a site once, himself, in a headed window on that profile:

    python3 -m draft_studio.capture login https://panel.example/

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
          "steps": [{"click": "<visible text>"} | {"wait": "<visible text>"}],
          "highlight": "<visible text>" | null}
    200 {"asset": "<name>-<sha12>.png", "md": "![](assets/<asset>)"}
    409 {"error": "needs-permission", "request": <request>}
    409 {"error": "login-required", "origin": "https://host", "url": <spec url>}
    409 {"error": "profile-busy"}            login window open, or a capture running
    400 {"error": "bad-spec", "detail": "url" | "name" | "steps" | "step-<n>" | "highlight" | "body"}
    422 {"error": "no-target", "step": n | null, "text": ..., "seen": [visible labels]}
    422 {"error": "ambiguous-target", "step": n | null, "text": ..., "count": n}
    422 {"error": "covered-target", "text": ...}
    502 {"error": "chromium-failed", "detail": ...}
    502 {"error": "unguarded-target", "kind": <target type>, "detail": ...}  no request interception
    503 {"error": "no-chromium"}
    504 {"error": "timeout"}                 $DRAFT_STUDIO_CAPTURE_TIMEOUT, default 45 s
    404 {"error": "not-found"}               no such draft
GET  /api/captures/pending      200 {"requests": [<request>, ...]} oldest first
GET  /api/captures/<rid>/shot   200 image/png: the screen with the refused target outlined
POST /api/captures/<rid>/grant  re-runs the capture with that step allowed, once;
                                same responses as POST .../capture; 409 not-pending
POST /api/captures/<rid>/deny   re-runs the capture with that refusal denied (see below):
                                same responses as POST .../capture; 409 not-pending, 404 not-found,
                                409 profile-busy (the request stays pending)

<request> = {"id": "<16 hex>", "draftId", "spec", "step": n, "stepText",
             "reason": "deny-list" | "submit" | "input" | "request" | "form-submit",
             "detail": "<stem>" | "<METHOD url>", "allow": <grant>, "grants": [<grant>],
             "denials": [<grant>, ...], "status": "pending" | "granted" | "denied", "created",
             "shot": "/api/captures/<rid>/shot"}
<grant>   = {"step": n, "click": bool, "requests": [["POST", "https://..."], ...]}  (denials: same shape)

A grant re-runs the whole capture from the start. Every allowance is spent by
one run: a re-run that reaches an earlier, already granted step asks again
(see pending.py), so nothing kuhy allowed once is ever sent twice.

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
  a grant is spent by one run, a denial never is. Denied requests apply on every step.
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

POST /api/drafts/<id>/propose   200 {"replacement", "from", "to", "stripped": [...], "dropped": [<drop>]}
POST /api/drafts/<id>/brief | /intake   (first draft) 200 {"questions": [], "state", "stripped", "dropped"}
    both: 202 {"held": <held>} while a capture waits on kuhy or the profile
GET  /api/drafts/<id>/held      200 {"held": [<held>], "released": [<released>]} oldest first
POST /api/drafts/<id>/held      {"retry": "<held id>"}: re-run its login/busy captures (after
                                `capture login`); 200 with the new listing, 404 no such held id
                                {"collect": ["<held id>", ...]}: forget released ones the client
                                showed; 200 {"collected": n}; 400 {"error": "bad-held"}
POST /api/drafts/<id>/held/<held id>/login   body {"origin": "https://host"} (one of <held>.login)
                                202 {"started": true}: `python3 -m draft_studio.capture login <origin>`
                                runs detached; when kuhy closes its window the server retries that
                                hold by itself (client: keep polling GET .../held)
                                409 {"error": "profile-busy"}   login window open or profile held
                                400 {"error": "bad-origin"}     origin not in the hold's `login` list
                                404 {"error": "not-found"}      no such draft or held id
                                502 {"error": "login-failed"}   the login process did not start
                                (loginapi.py)

<held>     = {"id": "<16 hex>", "kind": "proposal" | "draft", "from": n | null, "to": n | null,
              "requests": [<request>],  pending permission requests: grant/deny them as above
              "login": ["https://origin", ...],  log in, then POST .../held {"retry": id}
              "busy": bool,             profile was busy (login window open): retry
              "tries": n}               retries run so far (a login window closed without a login
                                        still bumps it, so the client sees the hold move)
<released> = proposal: {"id", "kind", "replacement", "from", "to", "span", "previous",
                        "stripped", "dropped"}  apply only while the doc still holds `span`
             draft:    {"id", "kind", "questions": [], "state", "stripped", "dropped"}
                        already saved server-side (phase "editing")
<drop>     = {"key": "<capture key>", "reason": "expired" | "url" | "bad-spec" |
              "no-request" | "<capture error code>"}

A grant or deny (POST /api/captures/<rid>/grant|deny) answers as before and
also moves the held answer that waits on <rid>: a 409 needs-permission
re-links it to the new request, a 200 fills its image, and once no capture
waits the answer is released. A deny never drops the image line.
"""
