"""The page helper's exact element lookup (spliced into script.py's window.__ds, its helpers in scope).

- deep(text): the deepest visible elements whose text, aria-label, title, alt or
  button value equals `text` (case and whitespace folded).
- find(text): those, mapped to their clickable ancestor, once each (as always).
- aim(aim): aim.py's object. `role: "button"` takes every visible button instead
  of a text (icon-only buttons). `within`: the scope is the smallest element
  around the one anchor (runner.py checks there is exactly one) that holds a
  candidate other than itself. `nth` picks by document order (1 first, -1 last).
- anchors(text): how many elements show `text`, for that check.
- around(ref): "<last heading before it> | <text of the smallest ancestor whose
  text differs from its own (its group, its row)>", for an ambiguous-target
  error: in a flat sidebar the heading still tells the matches apart.
- labels(): labels of the visible clickable elements, for a no-target error.
"""

FIND = r"""
  const deep = (text) => {
    const want = norm(text);
    const hits = [...document.querySelectorAll('body *')].filter(
      (el) => !['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(el.tagName) &&
        labels(el).some((l) => norm(l) === want) && visible(el));
    return hits.filter((el) => !hits.some((o) => o !== el && el.contains(o)));
  };
  const clickable = (els) => {
    const targets = [];
    for (const el of els) {
      const t = el.closest(CLICKABLE) || el;
      if (!targets.includes(t)) targets.push(t);
    }
    return targets;
  };
  const BUTTON = 'button,[role=button],input[type=button],input[type=submit],input[type=image]';
  const pick = (a) => {
    let pool = a.role ? [...document.querySelectorAll(BUTTON)].filter(visible) : clickable(deep(a.text));
    if (a.within) {
      const anchor = deep(a.within)[0];
      const inside = (box) => pool.filter((t) => t !== box && box.contains(t));
      let box = anchor;
      while (box && !inside(box).length) box = box.parentElement;
      pool = box ? inside(box) : [];
    }
    if (a.nth) {
      const i = a.nth > 0 ? a.nth - 1 : pool.length + a.nth;
      pool = i >= 0 && i < pool.length ? [pool[i]] : [];
    }
    return pool;
  };
  st.aim = (a) => pick(a).map((t) => describe(t, st.refs.push(t) - 1));
  st.find = (text) => st.aim({ text });
  st.anchors = (text) => deep(text).length;
  const HEADS = 'h1,h2,h3,h4,h5,h6,[role=heading],legend,th';
  st.around = (ref) => {
    const el = st.refs[ref];
    if (!el) return '';
    let box = el;
    const own = norm(el.innerText);
    while (box.parentElement && norm(box.innerText) === own) box = box.parentElement;
    const heads = [...document.querySelectorAll(HEADS)].filter(
      (h) => !h.contains(el) && h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
    const head = heads.length ? norm(heads[heads.length - 1].innerText) : '';
    return [head, norm(box.innerText).slice(0, 120)].filter(Boolean).join(' | ');
  };
  st.labels = () => [...document.querySelectorAll(CLICKABLE)].filter(visible)
    .map((el) => norm(labels(el).find((l) => norm(l)))).filter(Boolean).slice(0, 60);
"""
