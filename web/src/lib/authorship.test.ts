import { describe, expect, it } from 'vitest'
import { addRange, applyChanges, normalize, subtractRange } from './authorship'

describe('authorship ranges (claude-written spans; everything else is yours)', () => {
  it('normalizes: sorts, merges touching, drops empty', () => {
    expect(normalize([[5, 7], [0, 2], [2, 3], [9, 9]])).toEqual([[0, 3], [5, 7]])
  })

  it('subtracts a span, splitting a range', () => {
    expect(subtractRange([[0, 10]], 3, 5)).toEqual([[0, 3], [5, 10]])
    expect(subtractRange([[0, 10]], 0, 10)).toEqual([])
    expect(subtractRange([[2, 4]], 5, 9)).toEqual([[2, 4]])
    expect(subtractRange([[2, 4]], 3, 3)).toEqual([[2, 4]])
  })

  it('adds a span, merging neighbours', () => {
    expect(addRange([[0, 2], [5, 7]], 2, 5)).toEqual([[0, 7]])
    expect(addRange([], 1, 1)).toEqual([])
  })

  const shift = (at: number, by: number) => (pos: number, assoc: number) =>
    pos < at || (pos === at && assoc < 0) ? pos : pos + by

  it('your typing inside claude text is cut out of the claude range', () => {
    // "Hello world" all claude; you insert "big " at 6.
    const out = applyChanges([[0, 11]], [{ fromB: 6, toB: 10 }], shift(6, 4), false)
    expect(out).toEqual([[0, 6], [10, 15]])
  })

  it('your typing at a claude boundary never becomes claude', () => {
    expect(applyChanges([[0, 5]], [{ fromB: 5, toB: 8 }], shift(5, 3), false)).toEqual([[0, 5]])
    expect(applyChanges([[3, 5]], [{ fromB: 3, toB: 5 }], shift(3, 2), false)).toEqual([[5, 7]])
  })

  it('an accepted claude insert is tinted claude and leaves your text alone', () => {
    // "you claude": claude at [4,10]; accept inserts "new " at 0 (replacing nothing).
    const out = applyChanges([[4, 10]], [{ fromB: 0, toB: 4 }], shift(0, 4), true)
    expect(out).toEqual([[0, 4], [8, 14]])
  })

  it('deleting everything in a range drops it', () => {
    const del = (pos: number) => (pos <= 2 ? pos : pos >= 5 ? pos - 3 : 2)
    expect(applyChanges([[2, 5]], [], del, false)).toEqual([])
  })
})
