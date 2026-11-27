import { history, redo, undo } from '@codemirror/commands'
import { EditorState, Transaction } from '@codemirror/state'
import { describe, expect, it } from 'vitest'
import {
  authorship,
  authorshipExtensions,
  loadDoc,
  remoteEdit,
  setAuthorship,
} from './editorState'

function mk(doc: string, claude: [number, number][] = []): { s: EditorState } {
  const s = EditorState.create({ doc, extensions: [history(), authorshipExtensions] })
  return { s: s.update(loadDoc(s, doc, claude, false)).state }
}

const run = (box: { s: EditorState }, cmd: typeof undo) =>
  cmd({ state: box.s, dispatch: (tr: Transaction) => (box.s = tr.state) })

const type = (box: { s: EditorState }, from: number, to: number, insert: string) => {
  box.s = box.s.update({ changes: { from, to, insert }, userEvent: 'input.type' }).state
}

describe('authorship field', () => {
  it('a tint snapshot past the end of the doc maps clamped', () => {
    const box = mk('Hello world', [[0, 11]])
    const effect = setAuthorship.of({ ranges: [[2, 40]], rev: 0 }).map(box.s.changes({ from: 0, insert: 'Oh ' }))
    expect(effect?.value.ranges).toEqual([[5, 14]])
  })

  it('undo after another tab\'s text maps the tint snapshot through it', () => {
    const box = mk('Hello world', [[6, 11]])
    type(box, 0, 0, 'Oh ')
    // The other tab put "big " before "Hello": not in this tab's undo history.
    box.s = box.s.update({
      changes: { from: 3, insert: 'big ' },
      effects: setAuthorship.of({ ranges: [[13, 18]], rev: box.s.field(authorship).rev }),
      annotations: [remoteEdit.of(true), Transaction.addToHistory.of(false)],
    }).state
    run(box, undo)
    expect(box.s.doc.toString()).toBe('big Hello world')
    expect(box.s.field(authorship).ranges).toEqual([[10, 15]])
  })

  it('loads ranges and cuts your typing out of claude text (C6, C14)', () => {
    const box = mk('Hello world', [[0, 11]])
    expect(box.s.field(authorship).ranges).toEqual([[0, 11]])
    type(box, 6, 6, 'big ')
    expect(box.s.field(authorship).ranges).toEqual([[0, 6], [10, 15]])
  })

  it('undo restores tinting with the text (C17)', () => {
    const box = mk('Hello world', [[0, 11]])
    type(box, 5, 5, '!!')
    expect(box.s.field(authorship).ranges).toEqual([[0, 5], [7, 13]])
    run(box, undo)
    expect(box.s.doc.toString()).toBe('Hello world')
    expect(box.s.field(authorship).ranges).toEqual([[0, 11]])
    run(box, redo)
    expect(box.s.doc.toString()).toBe('Hello!! world')
    expect(box.s.field(authorship).ranges).toEqual([[0, 5], [7, 13]])
  })

  it('loadDoc replaces the whole doc and its ranges; undoable when in history', () => {
    const box = mk('mine', [])
    box.s = box.s.update(loadDoc(box.s, 'old claude', [[0, 10]], true)).state
    expect(box.s.doc.toString()).toBe('old claude')
    expect(box.s.field(authorship).ranges).toEqual([[0, 10]])
    run(box, undo)
    expect(box.s.doc.toString()).toBe('mine')
    expect(box.s.field(authorship).ranges).toEqual([])
  })

  it('with several snapshots in one step, the oldest wins', () => {
    const box = mk('abcdef', [])
    const older = { ranges: [[0, 1]] as const, rev: 1 }
    const newer = { ranges: [[0, 2]] as const, rev: 5 }
    for (const effects of [[setAuthorship.of(newer), setAuthorship.of(older)], [setAuthorship.of(older), setAuthorship.of(newer)]]) {
      expect(box.s.update({ effects }).state.field(authorship).ranges).toEqual([[0, 1]])
    }
  })

  it('clamps loaded ranges to the doc', () => {
    const box = mk('abc', [[1, 99]])
    expect(box.s.field(authorship).ranges).toEqual([[1, 3]])
  })
})
