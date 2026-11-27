"""The page helper's exact element lookup (spliced into script.py's window.__ds, its helpers in scope).

- deep(text): the deepest visible elements whose text, aria-label, title, alt or
  button value equals `text` (case and whitespace folded).
- find(text): those, mapped to their clickable ancestor, once each (as always).
- aim(aim): aim.py's object. `role: "button"` takes every visible button instead
  of a text (icon-only buttons). `within`: the scope is the smallest element
  around the one anchor (runner.py checks there is exactly one) that holds a
  candidate other than itself. `nth` picks by document order (1 first, -1 last).
- anchors(text): how many elements show `text`, for that check.
- around(ref): the text of the smallest ancestor whose text differs from the
  element's own (its group, its row), for an ambiguous-target error.
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
  st.around = (ref) => {
    let el = st.refs[ref];
    const own = norm(el && el.innerText);
    while (el && el.parentElement && norm(el.innerText) === own) el = el.parentElement;
    return el ? norm(el.innerText).slice(0, 120) : '';
  };
  st.labels = () => [...document.querySelectorAll(CLICKABLE)].filter(visible)
    .map((el) => norm(labels(el).find((l) => norm(l)))).filter(Boolean).slice(0, 60);
"""
