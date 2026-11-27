/**
 * Claude tint and the inline word diffs of every pending proposal: per-hunk
 * accept/reject, plus one accept-all/reject-all pair after the last proposal
 * that acts on all of them.
 */
import type { EditorState, Range as CMRange } from '@codemirror/state'
import { Decoration, type DecorationSet, EditorView, WidgetType } from '@codemirror/view'
import { acceptHunks, authorship, type HunkPick, proposals, proposalsOf, rejectHunks } from './editorState'
import { label } from './i18n'
import { iconButton } from './icons'

export function labelled<T extends HTMLElement>(el: T): T {
  el.setAttribute('aria-label', label(el.dataset.label ?? ''))
  return el
}

/** Wire an accept and a reject button to `which`; mousedown keeps the selection. */
function pair(view: EditorView, which: HunkPick, ok: HTMLElement, no: HTMLElement): HTMLElement[] {
  ok.addEventListener('mousedown', (e) => {
    e.preventDefault()
    view.dispatch(acceptHunks(view.state, which))
  })
  no.addEventListener('mousedown', (e) => {
    e.preventDefault()
    view.dispatch(rejectHunks(view.state, which))
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
  }
  const last = list.at(-1)
  if (last) decos.push(Decoration.widget({ widget: new AllWidget(), side: 2 }).range(last.to))
  return Decoration.set(decos, true)
}

export const decorations = EditorView.decorations.compute([authorship, proposals, 'doc'], buildDecorations)
