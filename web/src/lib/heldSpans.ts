/**
 * Held spans in the text: a dashed outline in the hold's colour and a steady
 * spinner after it, so kuhy sees which text waits on which permission card
 * (editorCard.ts). `lit`: the span of the card under the pointer. The field
 * maps the marks through edits; the card repaints them on every listing.
 */
import { StateEffect, StateField, type Range } from '@codemirror/state'
import { Decoration, type DecorationSet, EditorView, WidgetType } from '@codemirror/view'
import { icon } from './icons'

export interface HeldSpan {
  readonly id: string
  readonly from: number
  readonly to: number
  readonly hue: string
  readonly lit: boolean
  /** No screenshot could be taken: the glyph is image-off, not the spinner. */
  readonly failed: boolean
}

export const setHeldSpans = StateEffect.define<readonly HeldSpan[]>()

class Spinner extends WidgetType {
  constructor(
    readonly hue: string,
    readonly failed: boolean,
  ) {
    super()
  }

  eq(other: Spinner): boolean {
    return other.hue === this.hue && other.failed === this.failed
  }

  toDOM(): HTMLElement {
    const dom = document.createElement('span')
    dom.className = 'held-glyph'
    dom.style.setProperty('--hc', this.hue)
    dom.setAttribute('aria-hidden', 'true')
    dom.innerHTML = icon(this.failed ? 'imageOff' : 'spinner')
    return dom
  }
}

function build(spans: readonly HeldSpan[], length: number): DecorationSet {
  const out: Range<Decoration>[] = []
  for (const s of spans) {
    if (s.from >= s.to || s.to > length) continue
    const attributes = { class: s.lit ? 'held-span link' : 'held-span', style: `--hc: ${s.hue}`, 'data-held': s.id }
    out.push(Decoration.mark({ attributes }).range(s.from, s.to))
    out.push(Decoration.widget({ widget: new Spinner(s.hue, s.failed), side: 1 }).range(s.to))
  }
  return Decoration.set(out, true)
}

export const heldSpans = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    for (const e of tr.effects) if (e.is(setHeldSpans)) return build(e.value, tr.state.doc.length)
    return deco.map(tr.changes)
  },
  provide: (f) => EditorView.decorations.from(f),
})
