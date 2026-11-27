import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { addSignal, clearSignal, pulse, signalExtensions, signals, signalsOf } from './signals'

const doc = 'alpha beta gamma'
const mkState = () => EditorState.create({ doc, extensions: [signalExtensions] })
const mkView = () => new EditorView({ state: mkState(), parent: document.body })
const glyphs = (view: EditorView) =>
  [...view.dom.querySelectorAll<HTMLElement>('.cm-signal-glyph')].map((g) => [g.dataset.signal, g.innerHTML.includes('<svg')])
const marks = (view: EditorView) =>
  [...view.dom.querySelectorAll<HTMLElement>('.cm-signal')].map((m) => [m.dataset.signal, m.textContent])

afterEach(() => {
  vi.useRealTimers()
  document.body.replaceChildren()
  delete document.body.dataset.signal
})

describe('signal state', () => {
  it('keeps each signal on its span through edits, until cleared', () => {
    let s = mkState()
    const a = addSignal(s, 6, 10, 'overlap')
    s = s.update(a.spec).state
    const b = addSignal(s, 0, 0, 'gone')
    s = s.update(b.spec).state
    expect(b.id).toBe(a.id + 1)
    s = s.update({ changes: { from: 0, insert: 'xx ' } }).state
    expect(signalsOf(s).map((g) => [g.from, g.to, g.why])).toEqual([[9, 13, 'overlap'], [3, 3, 'gone']])
    s = s.update(clearSignal(a.id)).state
    expect(signalsOf(s).map((g) => g.why)).toEqual(['gone'])
    expect(s.update(clearSignal(99)).state.field(signals)).toBe(s.field(signals))
  })
})

describe('signal view', () => {
  it('marks the span and puts a glyph after it: image-off for stripped links, a cross for a dropped answer', () => {
    const view = mkView()
    view.dispatch(addSignal(view.state, 6, 10, 'stripped').spec)
    view.dispatch(addSignal(view.state, 11, 16, 'overlap').spec)
    view.dispatch(addSignal(view.state, 0, 0, 'gone').spec)
    expect(marks(view)).toEqual([['stripped', 'beta'], ['overlap', 'gamma']])
    expect(glyphs(view)).toEqual([['gone', true], ['stripped', true], ['overlap', true]])
    const [gone, stripped, overlap] = [...view.dom.querySelectorAll('.cm-signal-glyph')].map((g) => g.innerHTML)
    expect(stripped).not.toBe(overlap)
    expect(gone).toBe(overlap)
    expect(view.dom.querySelector('.cm-signal-glyph')?.getAttribute('aria-hidden')).toBe('true')
  })

  it('pulse shows the signal for a moment, records it on the body, and a repeat stacks', () => {
    vi.useFakeTimers()
    const view = mkView()
    pulse(view, 0, 5, 'stripped', 1000)
    expect(document.body.dataset.signal).toBe('stripped')
    expect(glyphs(view)).toEqual([['stripped', true]])
    vi.advanceTimersByTime(500)
    pulse(view, 6, 10, 'gone')
    expect(document.body.dataset.signal).toBe('gone')
    vi.advanceTimersByTime(500)
    expect(glyphs(view)).toEqual([['gone', true]])
    vi.runAllTimers()
    expect(glyphs(view)).toEqual([])
  })

  it('a pulse outliving its view does nothing', () => {
    vi.useFakeTimers()
    const view = mkView()
    pulse(view, 0, 5, 'overlap', 10)
    view.destroy()
    expect(() => vi.runAllTimers()).not.toThrow()
  })
})
