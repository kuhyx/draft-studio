"""Screenshot capture: real screenshots of web pages for how-to drafts, never invented images.

Headless /usr/bin/chromium on its own persistent profile (<data dir>/capture-profile),
driven over DevTools on a port chromium picks (never 9222, never kuhy's display).
kuhy logs into a site once, himself, in a headed window on that profile:

    python3 -m draft_studio.capture login https://panel.example/

A capture only reads: it clicks elements named in the spec, never types, never
submits, and every non-GET request is failed before it is sent. Anything it
may not do becomes a permission request kuhy grants or denies.

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
    503 {"error": "no-chromium"}
    504 {"error": "timeout"}                 $DRAFT_STUDIO_CAPTURE_TIMEOUT, default 45 s
    404 {"error": "not-found"}               no such draft
GET  /api/captures/pending      200 {"requests": [<request>, ...]} oldest first
GET  /api/captures/<rid>/shot   200 image/png: the screen with the refused target outlined
POST /api/captures/<rid>/grant  re-runs the capture with that step allowed, once;
                                same responses as POST .../capture; 409 not-pending
POST /api/captures/<rid>/deny   200 {"cancelled": true, "request": <request>}; 409 not-pending

<request> = {"id": "<16 hex>", "draftId", "spec", "step": n, "stepText",
             "reason": "deny-list" | "submit" | "input" | "request" | "form-submit",
             "detail": "<stem>" | "<METHOD url>", "allow": <grant>, "grants": [<grant>],
             "status": "pending" | "granted" | "denied", "created", "shot": "/api/captures/<rid>/shot"}
<grant>   = {"step": n, "click": bool, "requests": [["POST", "https://..."], ...]}

A grant re-runs the whole capture from the start: earlier grants in `grants`
apply again on that run, each allowed request at most once.
"""
