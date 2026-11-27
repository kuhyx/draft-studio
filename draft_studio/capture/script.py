"""The page-side helper installed in every document a capture opens (window.__ds).

- find(text) / aim(aim) / anchors(text) / around(ref): exact element lookup,
  scoped and icon-only aims included (script_find.py, aim.py), described for policy.py.
- point(ref): scroll the element into view and hit-test its centre, so a
  cookie banner or modal never takes a click meant for something else.
- highlight(ref)/clear(): a fixed SVG overlay (red outline plus arrow) that
  ignores the pointer; removed before the page is used again.
- a capture-phase submit listener and a form.submit() wrapper block form
  submission (GET forms included) unless `allow` is set for a granted step.
"""

from draft_studio.capture.script_find import FIND

SCRIPT = (
    r"""
(() => {
  if (window.__ds) return;
  const st = { submits: [], allow: false, refs: [], overlay: null };
  Object.defineProperty(window, '__ds', { value: st });
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  addEventListener('submit', (e) => {
    if (st.allow) return;
    e.preventDefault();
    st.submits.push(String(e.target.action || location.href));
  }, true);
  const nativeSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () {
    if (st.allow) return nativeSubmit.call(this);
    st.submits.push(String(this.action || location.href));
  };
  st.takeSubmits = () => st.submits.splice(0);
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0;
  };
  const CLICKABLE = 'a[href],button,input,select,textarea,summary,label,[role=button],[role=link],' +
    '[role=menuitem],[role=tab],[role=option],[onclick],[contenteditable]';
  const BUTTONS = ['button', 'submit', 'reset', 'image'];
  const labels = (el) => [
    el.innerText, el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('alt'),
    el.tagName === 'INPUT' && BUTTONS.includes((el.type || '').toLowerCase()) ? el.value : '',
  ];
  const describe = (el, ref) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const field = (tag === 'input' && !BUTTONS.includes(type || 'text')) || tag === 'textarea' ||
      tag === 'select' || el.isContentEditable || (tag === 'label' && !!el.control);
    const submit = !!el.form && ((tag === 'button' && (type === '' || type === 'submit')) ||
      (tag === 'input' && (type === 'submit' || type === 'image')));
    return {
      ref, tag, field, submit,
      text: String(el.innerText || '').slice(0, 300),
      aria: el.getAttribute('aria-label') || '',
      title: el.getAttribute('title') || '',
      value: tag === 'input' ? String(el.value || '') : '',
    };
  };
"""
    + FIND
    + r"""  st.seen = (text) => !!document.body && norm(document.body.innerText).includes(norm(text));
  st.hasPassword = () => [...document.querySelectorAll('input[type=password]')].some(visible);
  const live = (ref) => {
    const el = st.refs[ref];
    if (!el || !el.isConnected) return null;
    el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    return el;
  };
  st.point = (ref) => {
    const el = live(ref);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const hit = document.elementFromPoint(x, y);
    return { x, y, hit: !!hit && (hit === el || el.contains(hit)) };
  };
  st.clear = () => { if (st.overlay) st.overlay.remove(); st.overlay = null; };
  st.highlight = (ref) => {
    st.clear();
    const el = live(ref);
    if (!el) return false;
    const r = el.getBoundingClientRect(), pad = 6, red = 'rgb(255,0,0)';
    const box = { l: r.left - pad, t: r.top - pad, w: r.width + 2 * pad, h: r.height + 2 * pad };
    const ns = 'http://www.w3.org/2000/svg';
    const make = (tag, attrs) => {
      const n = document.createElementNS(ns, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
      return n;
    };
    const svg = make('svg', { width: innerWidth, height: innerHeight, 'data-ds-overlay': '1' });
    svg.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none';
    svg.append(make('rect', { x: box.l, y: box.t, width: box.w, height: box.h, rx: 6, fill: 'none',
      stroke: red, 'stroke-width': 4 }));
    const dx = box.l + box.w / 2 > innerWidth / 2 ? -1 : 1, dy = box.t + box.h / 2 > innerHeight / 2 ? -1 : 1;
    const tip = { x: dx < 0 ? box.l - 4 : box.l + box.w + 4, y: dy < 0 ? box.t - 4 : box.t + box.h + 4 };
    const clamp = (v, max) => Math.min(Math.max(v, 12), max - 12);
    const from = { x: clamp(tip.x + dx * 110, innerWidth), y: clamp(tip.y + dy * 110, innerHeight) };
    const a = Math.atan2(tip.y - from.y, tip.x - from.x), head = 24;
    svg.append(make('line', { x1: from.x, y1: from.y, x2: tip.x - Math.cos(a) * head * 0.6,
      y2: tip.y - Math.sin(a) * head * 0.6, stroke: red, 'stroke-width': 6, 'stroke-linecap': 'round' }));
    const wing = (s) => `${tip.x - head * Math.cos(a + s)},${tip.y - head * Math.sin(a + s)}`;
    svg.append(make('polygon', { points: `${tip.x},${tip.y} ${wing(0.45)} ${wing(-0.45)}`, fill: red }));
    document.documentElement.append(svg);
    st.overlay = svg;
    return new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(() => ok(true))));
  };
})()"""
)
