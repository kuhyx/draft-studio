import { EditorState, StateEffect } from '@codemirror/state'
import type { Decoration, DecorationSet } from '@codemirror/view'
import { describe, expect, it } from 'vitest'
import { heldSpans, setHeldSpans, type HeldSpan } from './heldSpans'

const span = (over: Partial<HeldSpan> = {}): HeldSpan => ({ id: 'h1', from: 0, to: 5, hue: 'var(--accent)', lit: false, ...over })

function decos(set: DecorationSet): { from: number; to: number; deco: Decoration }[] {
  const out: { from: number; to: number; deco: Decoration }[] = []
  set.between(0, 1e9, (from, to, deco) => {
    out.push({ from, to, deco })
  })
  return out
}

const start = () => EditorState.create({ doc: 'alpha beta gamma', extensions: [heldSpans] })

describe('held spans', () => {
  it('outlines each held span in its colour and puts a spinner after it', () => {
    const s = start().update({ effects: setHeldSpans.of([span(), span({ id: 'h2', from: 6, to: 10, hue: 'var(--cat-2)', lit: true })]) }).state
    const got = decos(s.field(heldSpans))
    const marks = got.filter((d) => d.from !== d.to)
    expect(marks.map((d) => [d.from, d.to, (d.deco.spec as { attributes: unknown }).attributes])).toEqual([
      [0, 5, { class: 'held-span', style: '--hc: var(--accent)', 'data-held': 'h1' }],
      [6, 10, { class: 'held-span link', style: '--hc: var(--cat-2)', 'data-held': 'h2' }],
    ])
    const widgets = got.filter((d) => d.from === d.to)
    expect(widgets.map((d) => d.from)).toEqual([5, 10])
    const dom = (widgets[1]?.deco.spec as { widget: { toDOM: () => HTMLElement } }).widget.toDOM()
    expect([dom.className, dom.style.getPropertyValue('--hc'), dom.querySelector('svg') !== null]).toEqual([
      'held-glyph',
      'var(--cat-2)',
      true,
    ])
  })

  it('maps through edits, and skips empty or out-of-range spans', () => {
    let s = start().update({ effects: setHeldSpans.of([span({ from: 6, to: 10 }), span({ id: 'e', from: 3, to: 3 }), span({ id: 'x', from: 2, to: 99 })]) }).state
    s = s.update({ changes: { from: 0, insert: 'XX' }, effects: StateEffect.define().of(null) }).state
    const marks = decos(s.field(heldSpans)).filter((d) => d.from !== d.to)
    expect(marks.map((d) => [d.from, d.to])).toEqual([[8, 12]])
  })

  it('the same spinner widget is reused for an equal colour', () => {
    const one = start().update({ effects: setHeldSpans.of([span()]) }).state
    const two = one.update({ effects: setHeldSpans.of([span()]) }).state
    const w = (s: EditorState) =>
      (decos(s.field(heldSpans)).find((d) => d.from === d.to)?.deco.spec as { widget: { eq: (o: unknown) => boolean } }).widget
    expect(w(one).eq(w(two))).toBe(true)
    const other = one.update({ effects: setHeldSpans.of([span({ hue: 'red' })]) }).state
    expect(w(one).eq(w(other))).toBe(false)
  })
})
