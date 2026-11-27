import { ChangeSet, Text } from '@codemirror/state'
import fc from 'fast-check'
import { describe, expect, it } from 'vitest'
import { applyChanges, normalize, type Range } from './authorship'
import { changesBetween, merge } from './sync'

const run = (cs: ChangeSet, doc: string) => cs.apply(Text.of(doc.split('\n'))).toString()
const edit = (doc: string, specs: { from: number; to: number; insert: string }[]) => ChangeSet.of(specs, doc.length)
/** Local changes typed by you: Claude ranges lose whatever they cover. */
const typed = (ranges: readonly Range[], cs: ChangeSet): Range[] => {
  const ins: { fromB: number; toB: number }[] = []
  cs.iterChangedRanges((_a, _b, fromB, toB) => ins.push({ fromB, toB }))
  return applyChanges(ranges, ins, (p, a) => cs.mapPos(p, a), false)
}

describe('changesBetween', () => {
  it('turns two texts into the changes between them', () => {
    expect(run(changesBetween('one two three', 'one 2 three!'), 'one two three')).toBe('one 2 three!')
    expect(changesBetween('same', 'same').empty).toBe(true)
  })
})

describe('merge', () => {
  it('keeps both tabs edits', () => {
    const base = 'alpha beta gamma'
    const local = edit(base, [{ from: 0, to: 0, insert: 'A ' }])
    const server = { text: 'alpha beta gamma Z', claude: [] }
    const m = merge(base, local, [], server)
    const mine = run(local, base)
    expect(run(m.remote, mine)).toBe('A alpha beta gamma Z')
    expect(run(m.local, server.text)).toBe('A alpha beta gamma Z')
  })

  it('takes the server state as is when nothing is unsaved', () => {
    const base = 'abc'
    const m = merge(base, ChangeSet.empty(3), [[0, 3]], { text: 'abXc', claude: [[0, 1]] })
    expect(run(m.remote, base)).toBe('abXc')
    expect(m.ranges).toEqual([[0, 1]])
    expect(m.local.empty).toBe(true)
  })

  it('tints Claude text the other tab accepted, never your typing', () => {
    const base = 'hello world'
    const local = edit(base, [{ from: 0, to: 0, insert: 'me ' }])
    // The other tab accepted Claude's "big " before "world".
    const server = { text: 'hello big world', claude: [[6, 10]] as Range[] }
    const m = merge(base, local, [], server)
    const merged = run(m.remote, run(local, base))
    expect(merged).toBe('me hello big world')
    expect(m.ranges).toEqual([[9, 13]])
  })

  it('cuts your typing inside Claude text out of its range', () => {
    const base = 'claude text'
    const local = edit(base, [{ from: 6, to: 6, insert: 'X' }])
    const mine = typed([[0, 11]], local)
    const m = merge(base, local, mine, { text: 'claude text!', claude: [[0, 11]] })
    expect(run(m.remote, run(local, base))).toBe('claudeX text!')
    expect(m.ranges).toEqual([[0, 6], [7, 12]])
  })
})

// -- property: random concurrent edit sequences ------------------------------------

const alphabet = fc.constantFrom('a', 'b', ' ', '\n')
const baseArb = fc.array(alphabet, { maxLength: 30 }).map((cs) => cs.join(''))

/** Non-overlapping edits of `doc`, each inserting a unique marker `<Pk>` (no base chars in it). */
function editsArb(doc: string, tag: string) {
  return fc
    .uniqueArray(fc.tuple(fc.nat(doc.length), fc.nat(3), fc.boolean()), { maxLength: 4, selector: (t) => t[0] })
    .map((raw) => {
      const sorted = raw.toSorted((x, y) => x[0] - y[0])
      const specs: { from: number; to: number; insert: string }[] = []
      let floor = 0
      sorted.forEach(([at, del, ins], k) => {
        const from = Math.max(at, floor)
        const to = Math.min(from + del, doc.length)
        specs.push({ from, to, insert: ins ? `<${tag}${String(k)}>` : '' })
        floor = to + 1
      })
      return specs.filter((s) => s.from <= doc.length)
    })
}

const rangesArb = (len: number) =>
  fc.array(fc.tuple(fc.nat(len), fc.nat(len)), { maxLength: 4 }).map((ps) =>
    normalize(ps.map(([a, b]) => [Math.min(a, b), Math.max(a, b)] as const)),
  )

const caseArb = baseArb
  .chain((base) => fc.tuple(fc.constant(base), editsArb(base, 'L'), editsArb(base, 'S'), rangesArb(base.length)))
  .chain(([base, local, server, baseRanges]) => {
    const theirs = run(edit(base, server), base)
    return fc.record({
      base: fc.constant(base),
      local: fc.constant(local),
      server: fc.constant(server),
      baseRanges: fc.constant(baseRanges),
      serverRanges: rangesArb(theirs.length),
    })
  })

const valid = (ranges: readonly Range[], len: number) =>
  ranges.every(([a, b], i) => 0 <= a && a < b && b <= len && (i === 0 || (ranges[i - 1]?.[1] ?? 0) < a))

describe('merge (property)', () => {
  it('both tabs edits survive, ranges stay valid, your typing is never Claude', () => {
    fc.assert(
      fc.property(caseArb, ({ base, local, server, baseRanges, serverRanges }) => {
        const a = edit(base, local)
        const b = edit(base, server)
        const theirs = run(b, base)
        const mine = run(a, base)
        const m = merge(base, a, typed(baseRanges, a), { text: theirs, claude: serverRanges })
        const merged = run(m.remote, mine)
        expect(run(m.local, theirs)).toBe(merged)
        for (const s of [...local, ...server]) if (s.insert) expect(merged).toContain(s.insert)
        expect(valid(m.ranges, merged.length)).toBe(true)
        for (const s of local) {
          if (!s.insert) continue
          const at = merged.indexOf(s.insert)
          const inClaude = m.ranges.some(([x, y]) => x < at + s.insert.length && at < y)
          expect(inClaude).toBe(false)
        }
      }),
      { numRuns: 400, seed: 20261127 },
    )
  })

  it('with nothing unsaved, the tab ends exactly on the server state', () => {
    fc.assert(
      fc.property(caseArb, ({ base, server, serverRanges: claude }) => {
        const theirs = run(edit(base, server), base)
        const m = merge(base, ChangeSet.empty(base.length), [], { text: theirs, claude })
        expect(run(m.remote, base)).toBe(theirs)
        expect(m.ranges).toEqual(claude)
      }),
      { numRuns: 200, seed: 7 },
    )
  })
})
