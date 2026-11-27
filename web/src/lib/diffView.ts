/**
 * Claude tint and the inline word diffs of every pending proposal: per-hunk
 * accept/reject, plus one accept-all/reject-all pair after the last proposal
 * that acts on all of them.
 */
import type { EditorState, Range as CMRange } from '@codemirror/state'
import { Decoration, type DecorationSet, EditorView, WidgetType } from '@codemirror/view'
import { authorship, type HunkPick, proposals, proposalsOf, resolver } from './editorState'
import { label } from './i18n'
import { icon, iconButton } from './icons'

export function labelled<T extends HTMLElement>(el: T): T {
  el.setAttribute('aria-label', label(el.dataset.label ?? ''))
  return el
}

/** Wire an accept and a reject button to `which` (the page asks the server); mousedown keeps the selection. */
function pair(view: EditorView, which: HunkPick, ok: HTMLElement, no: HTMLElement): HTMLElement[] {
  ok.addEventListener('mousedown', (e) => {
    e.preventDefault()
    view.state.facet(resolver)('accept', which)
  })
  no.addEventListener('mousedown', (e) => {
    e.preventDefault()
    view.state.facet(resolver)('reject', which)
  })
  return [labelled(ok), labelled(no)]
}

class InsertWidget extends WidgetType {
  constructor(
    readonly text: string,
    readonly id: number,
    readonly index: number,
  ) {
    super()
  }
  override eq(other: InsertWidget): boolean {
    return other.text === this.text && other.id === this.id && other.index === this.index
  }
  toDOM(view: EditorView): HTMLElement {
    const wrap = document.createElement('span')
    wrap.className = 'cm-hunk'
    wrap.dataset.proposal = String(this.id)
    wrap.dataset.hunk = String(this.index)
    const ins = document.createElement('ins')
    ins.className = 'cm-ins'
    ins.textContent = this.text
    const ok = iconButton('check', 'acceptHunk', 'hunk-accept')
    const no = iconButton('x', 'rejectHunk', 'hunk-reject')
    wrap.append(ins, ...pair(view, { id: this.id, hunks: [this.index] }, ok, no))
    return wrap
  }
  override ignoreEvent(): boolean {
    return true
  }
}

/** A proposal that lacks screenshots Claude asked for: the image-off glyph, its keys as data. */
class MissingWidget extends WidgetType {
  constructor(readonly keys: readonly string[]) {
    super()
  }
  override eq(other: MissingWidget): boolean {
    return other.keys.join() === this.keys.join()
  }
  toDOM(): HTMLElement {
    const el = document.createElement('span')
    el.className = 'cm-missing'
    el.dataset.label = 'missingImage'
    el.dataset.missing = this.keys.join(' ')
    el.setAttribute('role', 'img')
    el.setAttribute('aria-label', label('missingImage'))
    el.innerHTML = icon('imageOff')
    return el
  }
  override ignoreEvent(): boolean {
    return true
  }
}

class AllWidget extends WidgetType {
  override eq(): boolean {
    return true
  }
  toDOM(view: EditorView): HTMLElement {
    const wrap = document.createElement('span')
    wrap.className = 'cm-hunk-all'
    const ok = iconButton('checkAll', 'acceptAll', 'accept-all')
    const no = iconButton('xAll', 'rejectAll', 'reject-all')
    wrap.append(...pair(view, 'all', ok, no))
    return wrap
  }
  override ignoreEvent(): boolean {
    return true
  }
}

const claudeMark = Decoration.mark({ class: 'cm-claude' })
const delMark = Decoration.mark({ class: 'cm-del', tagName: 'del' })

function buildDecorations(state: EditorState): DecorationSet {
  const decos: CMRange<Decoration>[] = []
  for (const [a, b] of state.field(authorship).ranges) decos.push(claudeMark.range(a, b))
  const list = proposalsOf(state)
  for (const p of list) {
    const spanMark = Decoration.mark({ class: 'cm-proposal', attributes: { 'data-proposal': String(p.id) } })
    if (p.to > p.from) decos.push(spanMark.range(p.from, p.to))
    p.hunks.forEach((h, i) => {
      if (h.to > h.from) decos.push(delMark.range(h.from, h.to))
      decos.push(Decoration.widget({ widget: new InsertWidget(h.insert, p.id, i), side: 1 }).range(h.to))
    })
    if (p.missing.length > 0) decos.push(Decoration.widget({ widget: new MissingWidget(p.missing), side: 1 }).range(p.to))
  }
  const last = list.at(-1)
  if (last) decos.push(Decoration.widget({ widget: new AllWidget(), side: 2 }).range(last.to))
  return Decoration.set(decos, true)
}

export const decorations = EditorView.decorations.compute([authorship, proposals, 'doc'], buildDecorations)
