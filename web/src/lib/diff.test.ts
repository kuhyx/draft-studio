import { describe, expect, it } from 'vitest'
import { applyHunks, wordHunks } from './diff'

describe('word-level hunks', () => {
  it('finds one hunk per changed run, positioned in the document', () => {
    const hunks = wordHunks('one two three', 'one 2 three better', 10)
    expect(hunks).toEqual([
      { from: 14, to: 17, insert: '2' },
      { from: 23, to: 23, insert: ' better' },
    ])
  })

  it('no change gives no hunks', () => {
    expect(wordHunks('same text', 'same text', 0)).toEqual([])
  })

  it('pure deletion and full replacement', () => {
    expect(wordHunks('a b c', 'a c', 0)).toEqual([{ from: 2, to: 4, insert: '' }])
    expect(wordHunks('x', 'y', 0)).toEqual([{ from: 0, to: 1, insert: 'y' }])
  })

  it('applying all hunks reproduces the replacement', () => {
    const doc = 'pre one two three post'
    const hunks = wordHunks('one two three', 'one 2 three better', 4)
    expect(applyHunks(doc, hunks)).toBe('pre one 2 three better post')
    expect(applyHunks(doc, [hunks[1]])).toBe('pre one two three better post')
  })
})
