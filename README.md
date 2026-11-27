# draft-studio

Write text drafts (mails, notes) together with Claude, in a local web page.

1. Describe what you need. Claude asks its questions as input fields, then writes a first draft.
2. Select any span (one character up to the whole text) and ask Claude about it,
   ask for a rewrite, or give an instruction. Rewrites come back as an inline word diff
   you accept or reject per change, or all at once. Several can wait at the same time.
   Select text inside a waiting one and give an instruction ("shorter"): Claude revises
   its own proposal in place.
3. Edit freely. Your text is plain, Claude's has a subtle tint; Claude never changes
   your text unless you accept it, and an edit inside a pending span discards the proposal.
4. Everything autosaves as markdown with full history: undo walks back through it,
   and any version can be restored.
5. For a mail, the Gmail icon saves (or updates) a Gmail draft. Nothing is sent.
6. Paste or drop an image (PNG, JPEG, GIF, WebP, up to 10 MB) into the text. It is stored
   with the draft and referenced as `![alt](assets/<name>)`; the image shows under that line.
   Scripts can upload with `POST /api/drafts/<id>/assets?name=<file>` (raw bytes, `image/*`).

## Install

```bash
git clone https://github.com/kuhyx/draft-studio ~/src/draft-studio
bash ~/src/draft-studio/scripts/install.sh   # systemd --user unit, http://127.0.0.1:8794/
```

Needs Python 3.13+, Node 24+ with pnpm, and the `claude` CLI logged in
(`~/.local/bin/claude`). Claude runs headless (`claude -p`) on your subscription.
Gmail drafts go through the claude.ai Gmail connector.

## Data

Drafts live in `~/.local/share/draft-studio/` (`drafts/<id>/draft.md`, `meta.json`,
`history.jsonl`, images in `assets/`; deleted drafts move to `trash/` with their images). Nothing you write is stored in
this repo, and the pre-commit guard refuses such files.

## Languages

The interface has no visible text. Screen-reader labels and errors come from
`locales/<tag>.json`; add a language by adding one file (set `"dir": "rtl"` for
right-to-left). Each draft has its own language tag, detected from your brief.
