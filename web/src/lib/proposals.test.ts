import { history, undo } from '@codemirror/commands'
import { EditorState, type Transaction } from '@codemirror/state'
import { describe, expect, it } from 'vitest'
import {
  acceptHunks,
  authorshipExtensions,
  loadDoc,
  type Proposal,
  proposalsOf,
  rejectHunks,
  reviseTarget,
  setProposalFor,
} from './editorState'

// Two independent spans: "one two three" [5, 18) and "four five six" [23, 36).
const doc = 'mine one two three and four five six end'
const A: readonly [number, number] = [5, 18]
const B: readonly [number, number] = [23, 36]

function mk(): { s: EditorState } {
  const s = EditorState.create({ doc, extensions: [history(), authorshipExtensions] })
  return { s: s.update(loadDoc(s, doc, [], false)).state }
}
const put = (box: { s: EditorState }, [from, to]: readonly [number, number], text: string, replaces: readonly number[] = []) => {
  box.s = box.s.update(setProposalFor(box.s, from, to, text, replaces)).state
}
const two = () => {
  const box = mk()
  put(box, A, 'one 2 three')
  put(box, B, 'four 5 six')
  return box
}
const ids = (box: { s: EditorState }) => proposalsOf(box.s).map((p) => p.id)
const first = (box: { s: EditorState }): Proposal => {
  const p = proposalsOf(box.s).at(0)
  if (!p) throw new Error('no proposal')
  return p
}
const type = (box: { s: EditorState }, from: number, to: number, insert: string) => {
  box.s = box.s.update({ changes: { from, to, insert }, userEvent: 'input.type' }).state
}

describe('several pending proposals', () => {
  it('a second answer elsewhere joins the first instead of replacing it', () => {
    const box = two()
    expect(proposalsOf(box.s).map((p) => [p.from, p.to])).toEqual([[5, 18], [23, 36]])
    expect(new Set(ids(box)).size).toBe(2)
    expect(box.s.doc.toString()).toBe(doc)
  })

  it('each is accepted or rejected on its own', () => {
    const box = two()
    const [a, b] = ids(box) as [number, number]
    box.s = box.s.update(acceptHunks(box.s, { id: a, hunks: [0] })).state
    expect(box.s.doc.toString()).toBe('mine one 2 three and four five six end')
    expect(ids(box)).toEqual([b])
    expect(first(box)).toMatchObject({ from: 21, to: 34 })
    box.s = box.s.update(rejectHunks(box.s, { id: b, hunks: [0] })).state
    expect(proposalsOf(box.s)).toEqual([])
    expect(box.s.doc.toString()).toBe('mine one 2 three and four five six end')
  })

  it('accept-all and reject-all act on every proposal', () => {
    const a = two()
    a.s = a.s.update(acceptHunks(a.s, 'all')).state
    expect(a.s.doc.toString()).toBe('mine one 2 three and four 5 six end')
    expect(proposalsOf(a.s)).toEqual([])
    const r = two()
    r.s = r.s.update(rejectHunks(r.s, 'all')).state
    expect(r.s.doc.toString()).toBe(doc)
    expect(proposalsOf(r.s)).toEqual([])
  })

  it('an unknown id is a no-op', () => {
    const box = two()
    expect(acceptHunks(box.s, { id: 99, hunks: [0] })).toEqual({})
    expect(rejectHunks(box.s, { id: 99, hunks: [0] })).toEqual({})
  })

  it('your edit in one span drops only that one; the other is mapped', () => {
    const box = two()
    type(box, 9, 12, 'TWO')
    expect(proposalsOf(box.s).map((p) => [p.from, p.to])).toEqual([[23, 36]])
    type(box, 0, 0, 'XY')
    box.s = box.s.update(acceptHunks(box.s, 'all')).state
    expect(box.s.doc.toString()).toBe('XYmine one TWO three and four 5 six end')
  })

  it('undo of accepting one leaves the other pending at the right place', () => {
    const box = two()
    const b = ids(box)[1]
    box.s = box.s.update(acceptHunks(box.s, { id: ids(box)[0], hunks: [0] })).state
    undo({ state: box.s, dispatch: (tr: Transaction) => (box.s = tr.state) })
    expect(box.s.doc.toString()).toBe(doc)
    expect(ids(box)).toEqual([b])
    expect(first(box)).toMatchObject({ from: 23, to: 36 })
    box.s = box.s.update(acceptHunks(box.s, 'all')).state
    expect(box.s.doc.toString()).toBe('mine one two three and four 5 six end')
  })

  it('an answer overlapping a proposal that landed meanwhile is dropped as stale', () => {
    const box = mk()
    put(box, A, 'one 2 three')
    put(box, [9, 22], 'TWO three and')
    expect(proposalsOf(box.s).map((p) => [p.from, p.to])).toEqual([[5, 18]])
  })

  it('loading a doc clears every proposal', () => {
    const box = two()
    box.s = box.s.update(loadDoc(box.s, 'new', [], false)).state
    expect(proposalsOf(box.s)).toEqual([])
  })
})

describe('commenting on a pending proposal', () => {
  it('no target outside every proposal', () => {
    const box = two()
    expect(reviseTarget(box.s, 0, 4)).toBeNull()
    expect(reviseTarget(box.s, 18, 23)).toBeNull()
  })

  it('inside one: its span, its id and the proposed text', () => {
    const box = two()
    expect(reviseTarget(box.s, 9, 12)).toEqual({ from: 5, to: 18, ids: [ids(box)[0]], previous: 'one 2 three' })
  })

  it('partly covering: the span widens to the union', () => {
    const box = two()
    expect(reviseTarget(box.s, 0, 8)).toMatchObject({ from: 0, to: 18, previous: 'mine one 2 three' })
    expect(reviseTarget(box.s, 14, 30)).toEqual({
      from: 5,
      to: 36,
      ids: ids(box),
      previous: 'one 2 three and four 5 six',
    })
  })

  it('previous text reflects hunks already accepted or rejected', () => {
    const box = mk()
    put(box, A, 'one 2 three better')
    box.s = box.s.update(rejectHunks(box.s, { id: first(box).id, hunks: [1] })).state
    expect(reviseTarget(box.s, 5, 8)?.previous).toBe('one 2 three')
  })

  it('a revision replaces the old proposal in place, same id', () => {
    const box = two()
    const [a, b] = ids(box) as [number, number]
    put(box, A, 'one three', [a])
    expect(ids(box)).toEqual([a, b])
    box.s = box.s.update(acceptHunks(box.s, { id: a, hunks: [0] })).state
    expect(box.s.doc.toString()).toBe('mine one three and four five six end')
  })

  it('a widened revision replaces every proposal it covers', () => {
    const box = two()
    const t = reviseTarget(box.s, 14, 30)
    if (!t) throw new Error('no target')
    put(box, [t.from, t.to], 'one, four', t.ids)
    expect(proposalsOf(box.s)).toHaveLength(1)
    box.s = box.s.update(acceptHunks(box.s, 'all')).state
    expect(box.s.doc.toString()).toBe('mine one, four end')
  })

  it('a revision of a proposal you rejected meanwhile is dropped', () => {
    const box = two()
    const a = ids(box)[0]
    box.s = box.s.update(rejectHunks(box.s, { id: a, hunks: [0] })).state
    put(box, A, 'one three', [a])
    expect(proposalsOf(box.s).map((p) => p.from)).toEqual([23])
  })

  it('a revision back to the original text just drops the proposal', () => {
    const box = two()
    put(box, A, 'one two three', [ids(box)[0]])
    expect(proposalsOf(box.s).map((p) => p.from)).toEqual([23])
  })
})
