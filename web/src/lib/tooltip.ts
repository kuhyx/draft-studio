/**
 * <ds-tip>: one shared tooltip bubble for every icon-only button
 * (`.icon-btn[data-label]`). It opens on mouse hover, keyboard focus and a
 * touch long-press, shows the short `tips` word from the locale catalog, and
 * for the five editing actions (DEMOS) a looping demo of a built-in sample.
 *
 * Trade-offs:
 * - The bubble is a manual popover so it renders in the top layer, above the
 *   modal history dialog; it is re-shown on every open to stay on top.
 * - aria-hidden + pointer-events:none: screen readers keep the aria-label and
 *   the bubble never steals a click or hit test.
 * - Touch: a long-press shows the bubble above the finger while held; lifting
 *   cancels the touch (preventDefault on touchend), so neither the click nor
 *   the mousedown handlers (hunk accept/reject) fire. A quick tap acts at once.
 */
import { tip } from './i18n'
import { icon } from './icons'
import { DEMOS, placeTip } from './tipPlace'

const TARGET = '.icon-btn[data-label]'
const HOVER_MS = 150
const LONG_PRESS_MS = 500
const MOVE_PX = 10

type Input = 'mouse' | 'touch'

function el(tag: string, cls: string, html = ''): HTMLElement {
  const e = document.createElement(tag)
  e.className = cls
  e.innerHTML = html
  return e
}

/** Built-in sample: abstract text lines, never the user's draft. */
function demoParts(key: string): HTMLElement[] {
  const line1 = el('div', 'd-line')
  const target = el('span', 'd-target')
  target.append(el('span', 'd-bar d-old'), el('span', 'd-bar d-new'))
  line1.append(el('span', 'd-bar d-w4'), target, el('span', 'd-bar d-w3'))
  const line2 = el('div', 'd-line')
  line2.append(el('span', 'd-bar d-w9'))
  const mark = el('span', 'd-mark', icon(key === 'acceptHunk' ? 'check' : key === 'rejectHunk' ? 'x' : key))
  const extra = el('div', 'd-line d-extra')
  extra.append(el('span', 'd-bar d-card'))
  return [line1, line2, mark, extra]
}

export class DsTip extends HTMLElement {
  private readonly demo = el('div', 'tip-demo')
  private readonly text = el('span', 'tip-text')
  private target: HTMLElement | null = null
  private input: Input = 'mouse'
  private frame = 0

  connectedCallback(): void {
    this.setAttribute('popover', 'manual')
    this.setAttribute('aria-hidden', 'true')
    if (!this.contains(this.text)) this.append(this.demo, this.text)
  }

  get current(): HTMLElement | null {
    return this.target
  }

  show(target: HTMLElement, input: Input): void {
    const key = target.dataset.label ?? ''
    this.target = target
    this.input = input
    this.text.textContent = tip(key)
    this.demo.hidden = !DEMOS.has(key)
    this.demo.dataset.demo = key
    // Rebuild so the loop restarts from its first frame on every open.
    this.demo.replaceChildren(...(DEMOS.has(key) ? demoParts(key) : []))
    if (this.matches(':popover-open')) this.hidePopover()
    this.showPopover()
    cancelAnimationFrame(this.frame)
    this.follow()
  }

  hide(): void {
    this.target = null
    cancelAnimationFrame(this.frame)
    if (this.matches(':popover-open')) this.hidePopover()
  }

  /** Track the button each frame (CodeMirror moves its toolbar a frame late). */
  private follow(): void {
    const t = this.target
    if (!t) return
    if (!t.isConnected || t.closest('[hidden]')) {
      this.hide()
      return
    }
    const a = t.getBoundingClientRect()
    const b = this.getBoundingClientRect()
    // The selection toolbar sits above the text it acts on: keep that text visible.
    const mode = t.closest('.sel-toolbar') ? 'touch' : this.input
    const p = placeTip(a, { w: b.width, h: b.height }, { w: innerWidth, h: innerHeight }, mode)
    this.style.left = `${String(p.x)}px`
    this.style.top = `${String(p.y)}px`
    this.dataset.side = p.side
    this.frame = requestAnimationFrame(() => {
      this.follow()
    })
  }
}

/** Wire the shared bubble into a page: hover, keyboard focus, long-press. */
export function installTips(doc: Document): DsTip {
  if (!customElements.get('ds-tip')) customElements.define('ds-tip', DsTip)
  const found = doc.querySelector<DsTip>('ds-tip')
  if (found) return found
  const bubble = doc.createElement('ds-tip') as DsTip
  doc.body.append(bubble)
  const find = (t: EventTarget | null): HTMLElement | null =>
    t instanceof Element ? t.closest<HTMLElement>(TARGET) : null

  let hoverTimer: ReturnType<typeof setTimeout> | undefined
  doc.addEventListener('pointerover', (e) => {
    if (e.pointerType !== 'mouse') return
    const t = find(e.target)
    if (!t || t === bubble.current) return
    clearTimeout(hoverTimer)
    hoverTimer = setTimeout(() => {
      bubble.show(t, 'mouse')
    }, HOVER_MS)
  }, true)
  doc.addEventListener('pointerout', (e) => {
    if (e.pointerType !== 'mouse') return
    const from = find(e.target)
    if (!from || (e.relatedTarget instanceof Node && from.contains(e.relatedTarget))) return
    clearTimeout(hoverTimer)
    if (bubble.current === from) bubble.hide()
  }, true)

  doc.addEventListener('focusin', (e) => {
    const t = find(e.target)
    if (t?.matches(':focus-visible')) bubble.show(t, 'mouse')
  }, true)
  doc.addEventListener('focusout', (e) => {
    const t = find(e.target)
    if (t && bubble.current === t) bubble.hide()
  }, true)
  doc.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') bubble.hide()
  }, true)

  let pressTimer: ReturnType<typeof setTimeout> | undefined
  let pressed = false
  let start = { x: 0, y: 0 }
  doc.addEventListener('touchstart', (e) => {
    const t = find(e.target)
    const touch = e.touches[0]
    clearTimeout(pressTimer)
    pressed = false
    if (!t || !touch || e.touches.length > 1) return
    start = { x: touch.clientX, y: touch.clientY }
    pressTimer = setTimeout(() => {
      pressed = true
      bubble.show(t, 'touch')
    }, LONG_PRESS_MS)
  }, { capture: true, passive: true })
  doc.addEventListener('touchmove', (e) => {
    const touch = e.touches[0]
    if (touch && Math.hypot(touch.clientX - start.x, touch.clientY - start.y) > MOVE_PX) clearTimeout(pressTimer)
  }, { capture: true, passive: true })
  const lift = (e: TouchEvent) => {
    clearTimeout(pressTimer)
    if (!pressed) return
    pressed = false
    // Cancels the compatibility mousedown/click, so a long-press never acts.
    if (e.cancelable) e.preventDefault()
    bubble.hide()
  }
  doc.addEventListener('touchend', lift, { capture: true, passive: false })
  doc.addEventListener('touchcancel', lift, { capture: true, passive: false })
  doc.addEventListener('contextmenu', (e) => {
    if (find(e.target)) e.preventDefault()
  }, true)
  addEventListener('scroll', () => {
    if (bubble.current && !pressed) bubble.hide()
  }, { capture: true, passive: true })
  return bubble
}
