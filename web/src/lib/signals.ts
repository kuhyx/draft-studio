/**
 * Signals: a short pulse on a span when Claude's answer did not land as sent,
 * so nothing is dropped silently. Icons only, no text (textgate):
 * - `stripped`: the proposal shows, but image links Claude made up were cut
 *   (kuhy: "fake images are worse than no image at all"); image-off glyph.
 * - `overlap`: the answer overlapped another pending proposal and was dropped.
 * - `gone`: a revision arrived for a proposal kuhy rejected meanwhile.
 * Both drops show a cross. Your own edit inside a span stays silent, as before:
 * you made that change yourself.
 */
import { type EditorState, type Extension, StateEffect, StateField, type TransactionSpec } from '@codemirror/state'
import { Decoration, EditorView, WidgetType } from '@codemirror/view'
import { icon } from './icons'

export type SignalWhy = 'stripped' | 'overlap' | 'gone'
export interface Signal {
  readonly id: number
  readonly from: number
  readonly to: number
  readonly why: SignalWhy
}
interface Signals {
  readonly list: readonly Signal[]
  readonly seq: number
}

/** Long enough to notice; the CSS fades the glyph over the same time. */
export const PULSE_MS = 2400

const add = StateEffect.define<Signal>()
const clear = StateEffect.define<number>()

export const signals = StateField.define<Signals>({
  create: () => ({ list: [], seq: 1 }),
  update(value, tr) {
    let { list, seq } = value
    if (tr.docChanged) {
      list = list.map((g) => {
        const from = tr.changes.mapPos(g.from, 1)
        return { ...g, from, to: Math.max(from, tr.changes.mapPos(g.to, -1)) }
      })
    }
    for (const e of tr.effects) {
      if (e.is(add)) {
        list = [...list, e.value]
        seq = e.value.id + 1
      } else if (e.is(clear) && list.some((g) => g.id === e.value)) {
        list = list.filter((g) => g.id !== e.value)
      }
    }
    return list === value.list && seq === value.seq ? value : { list, seq }
  },
})

export const signalsOf = (state: EditorState): readonly Signal[] => state.field(signals).list

/** Start a signal on [from, to); `id` clears it again. */
export function addSignal(state: EditorState, from: number, to: number, why: SignalWhy): { spec: TransactionSpec; id: number } {
  const id = state.field(signals).seq
  return { spec: { effects: add.of({ id, from, to, why }) }, id }
}

export const clearSignal = (id: number): TransactionSpec => ({ effects: clear.of(id) })

class Glyph extends WidgetType {
  constructor(readonly why: SignalWhy) {
    super()
  }
  override eq(other: Glyph): boolean {
    return other.why === this.why
  }
  toDOM(): HTMLElement {
    const el = document.createElement('span')
    el.className = 'cm-signal-glyph'
    el.dataset.signal = this.why
    el.setAttribute('aria-hidden', 'true')
    el.innerHTML = icon(this.why === 'stripped' ? 'imageOff' : 'x')
    return el
  }
}

const decorations = EditorView.decorations.compute([signals], (state) =>
  Decoration.set(
    signalsOf(state).flatMap((g) => [
      ...(g.to > g.from ? [Decoration.mark({ class: 'cm-signal', attributes: { 'data-signal': g.why } }).range(g.from, g.to)] : []),
      Decoration.widget({ widget: new Glyph(g.why), side: 3 }).range(g.to),
    ]),
    true,
  ),
)

export const signalExtensions: Extension = [signals, decorations]

/** Show a signal on [from, to) for `ms`; the body keeps the last one for the headless E2E. */
export function pulse(view: EditorView, from: number, to: number, why: SignalWhy, ms = PULSE_MS): void {
  const { spec, id } = addSignal(view.state, from, to, why)
  view.dispatch(spec)
  document.body.dataset.signal = why
  setTimeout(() => {
    // A view destroyed meanwhile (page left) has nothing left to clear.
    if (view.dom.isConnected) view.dispatch(clearSignal(id))
  }, ms)
}
