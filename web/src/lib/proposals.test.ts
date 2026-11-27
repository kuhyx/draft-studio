import { history } from '@codemirror/commands'
import { ChangeSet, EditorState } from '@codemirror/state'
import { describe, expect, it } from 'vitest'
import {
  authorshipExtensions,
  loadDoc,
  type Proposal,
  proposalsOf,
  resolutions,
  reviseTarget,
  resolver,
  type ServerProposal,
  syncProposals,
} from './editorState'

// Two independent spans: "one two three" [5, 18) and "four five six" [23, 36).
const doc = 'mine one two three and four five six end'
const A: ServerProposal = { id: 1, rev: 1, from: 5, to: 18, replacement: 'one 2 three' }
const B: ServerProposal = { id: 2, rev: 3, from: 23, to: 36, replacement: 'four 5 six' }

interface Box {
  s: EditorState
  local: ChangeSet
}
function mk(list: readonly ServerProposal[] = []): Box {
  const s = EditorState.create({ doc, extensions: [history(), authorshipExtensions] })
  const box = { s: s.update(loadDoc(s, doc, [], false)).state, local: ChangeSet.empty(doc.length) }
  show(box, list)
  return box
}
const show = (box: Box, list: readonly ServerProposal[]) => {
  box.s = box.s.update(syncProposals(box.s, list, box.local)).state
}
const two = () => mk([A, B])
const ids = (box: Box) => proposalsOf(box.s).map((p) => p.id)
const first = (box: Box): Proposal => {
  const p = proposalsOf(box.s).at(0)
  if (!p) throw new Error('no proposal')
  return p
}
const type = (box: Box, from: number, to: number, insert: string) => {
  box.s = box.s.update({ changes: { from, to, insert }, userEvent: 'input.type' }).state
}

describe('the server\'s proposals shown on the doc', () => {
  it('each becomes word hunks for its own span; nothing is applied', () => {
    const box = two()
    expect(proposalsOf(box.s).map((p) => [p.id, p.rev, p.from, p.to])).toEqual([[1, 1, 5, 18], [2, 3, 23, 36]])
    expect(first(box).hunks.map((h) => h.insert)).toEqual(['2'])
    expect(box.s.doc.toString()).toBe(doc)
  })

  it('an unchanged list changes nothing', () => {
    const box = two()
    expect(syncProposals(box.s, [A, B], box.local)).toEqual({})
  })

  it('unsaved edits map the spans, and one touching a span drops it here', () => {
    const box = mk()
    box.local = ChangeSet.of({ from: 0, insert: 'XY' }, doc.length)
    show(box, [A, B])
    expect(proposalsOf(box.s).map((p) => [p.from, p.to])).toEqual([[7, 20], [25, 38]])
    box.local = ChangeSet.of({ from: 9, to: 12, insert: 'TWO' }, doc.length)
    show(box, [A, B])
    expect(ids(box)).toEqual([2])
  })

  it('skips a span past the text and a replacement equal to its span', () => {
    const box = mk([{ ...A, to: 999 }, { ...B, replacement: 'four five six' }])
    expect(proposalsOf(box.s)).toEqual([])
  })

  it('your edit in one span drops only that one; the other is mapped', () => {
    const box = two()
    type(box, 9, 12, 'TWO')
    expect(proposalsOf(box.s).map((p) => [p.from, p.to])).toEqual([[23, 36]])
    type(box, 0, 0, 'XY')
    expect(first(box)).toMatchObject({ from: 25, to: 38 })
  })

  it('loading a doc clears every proposal', () => {
    const box = two()
    box.s = box.s.update(loadDoc(box.s, 'new', [], false)).state
    expect(proposalsOf(box.s)).toEqual([])
  })

  it('the resolver facet is a no-op until the page provides one', () => {
    expect(() => { box0().s.facet(resolver)('accept', 'all') }).not.toThrow()
  })
})
const box0 = () => mk()

describe('resolving hunks', () => {
  const withBetter = () => mk([{ ...A, replacement: 'one 2 three better' }])

  it('a proposal with every hunk picked is resolved whole, at its rev', () => {
    const box = two()
    expect(resolutions(box.s, 'accept', 'all')).toEqual([{ id: 1, rev: 1 }, { id: 2, rev: 3 }])
    expect(resolutions(box.s, 'reject', { id: 2, hunks: [0] })).toEqual([{ id: 2, rev: 3 }])
  })

  it('some hunks: accept sends the span with those applied, reject the span with the others', () => {
    const box = withBetter()
    expect(resolutions(box.s, 'accept', { id: 1, hunks: [0] })).toEqual([{ id: 1, rev: 1, text: 'one 2 three' }])
    expect(resolutions(box.s, 'reject', { id: 1, hunks: [0] })).toEqual([{ id: 1, rev: 1, text: 'one two three better' }])
  })

  it('an unknown id or no proposal asks for nothing', () => {
    expect(resolutions(two().s, 'accept', { id: 99, hunks: [0] })).toEqual([])
    expect(resolutions(mk().s, 'accept', 'all')).toEqual([])
  })
})

describe('commenting on a pending proposal', () => {
  it('no target outside every proposal', () => {
    const box = two()
    expect(reviseTarget(box.s, 0, 4)).toBeNull()
    expect(reviseTarget(box.s, 18, 23)).toBeNull()
  })

  it('inside one: its span, its id and the proposed text', () => {
    expect(reviseTarget(two().s, 9, 12)).toEqual({ from: 5, to: 18, ids: [1], previous: 'one 2 three' })
  })

  it('partly covering: the span widens to the union', () => {
    const box = two()
    expect(reviseTarget(box.s, 0, 8)).toMatchObject({ from: 0, to: 18, previous: 'mine one 2 three' })
    expect(reviseTarget(box.s, 14, 30)).toEqual({ from: 5, to: 36, ids: [1, 2], previous: 'one 2 three and four 5 six' })
  })
})
