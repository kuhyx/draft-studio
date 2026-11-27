/**
 * The CodeMirror view: markdown editing, Claude tint, the selection toolbar
 * and the inline word diff with per-hunk accept/reject.
 */
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { markdown } from '@codemirror/lang-markdown'
import { Compartment, type EditorState, type Range as CMRange, StateField } from '@codemirror/state'
import {
  Decoration,
  type DecorationSet,
  EditorView,
  keymap,
  showTooltip,
  type Tooltip,
  type ViewUpdate,
  WidgetType,
} from '@codemirror/view'
import { acceptHunks, authorship, authorshipExtensions, proposal, rejectHunks } from './editorState'
import { label } from './i18n'
import { iconButton } from './icons'
import { imageDecorations, imageDropHandlers, type OnImages } from './images'

export type SelectionAction = 'ask' | 'rewrite' | 'change'
export interface EditorHooks {
  /** Any editor update (autosave, stale-request tracking). */
  onUpdate: (u: ViewUpdate) => void
  /** Selection toolbar action on [from, to); instruction empty for rewrite. */
  onAction: (action: SelectionAction, from: number, to: number, instruction: string) => void
  /** Image files pasted or dropped at pos (empty: only unsupported files). */
  onImages: OnImages
}

function labelled<T extends HTMLElement>(el: T): T {
  el.setAttribute('aria-label', label(el.dataset.label ?? ''))
  return el
}

class InsertWidget extends WidgetType {
  constructor(
    readonly text: string,
    readonly index: number,
  ) {
    super()
  }
  override eq(other: InsertWidget): boolean {
    return other.text === this.text && other.index === this.index
  }
  toDOM(view: EditorView): HTMLElement {
    const wrap = document.createElement('span')
    wrap.className = 'cm-hunk'
    wrap.dataset.hunk = String(this.index)
    const ins = document.createElement('ins')
    ins.className = 'cm-ins'
    ins.textContent = this.text
    const ok = labelled(iconButton('check', 'acceptHunk', 'hunk-accept'))
    const no = labelled(iconButton('x', 'rejectHunk', 'hunk-reject'))
    ok.addEventListener('mousedown', (e) => {
      e.preventDefault()
      view.dispatch(acceptHunks(view.state, [this.index]))
    })
    no.addEventListener('mousedown', (e) => {
      e.preventDefault()
      view.dispatch(rejectHunks(view.state, [this.index]))
    })
    wrap.append(ins, ok, no)
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
    const ok = labelled(iconButton('checkAll', 'acceptAll', 'accept-all'))
    const no = labelled(iconButton('xAll', 'rejectAll', 'reject-all'))
    ok.addEventListener('mousedown', (e) => {
      e.preventDefault()
      view.dispatch(acceptHunks(view.state, 'all'))
    })
    no.addEventListener('mousedown', (e) => {
      e.preventDefault()
      view.dispatch(rejectHunks(view.state, 'all'))
    })
    wrap.append(ok, no)
    return wrap
  }
  override ignoreEvent(): boolean {
    return true
  }
}

const claudeMark = Decoration.mark({ class: 'cm-claude' })
const delMark = Decoration.mark({ class: 'cm-del', tagName: 'del' })
const spanMark = Decoration.mark({ class: 'cm-proposal' })

function buildDecorations(state: EditorState): DecorationSet {
  const decos: CMRange<Decoration>[] = []
  for (const [a, b] of state.field(authorship).ranges) decos.push(claudeMark.range(a, b))
  const p = state.field(proposal)
  if (p) {
    if (p.to > p.from) decos.push(spanMark.range(p.from, p.to))
    p.hunks.forEach((h, i) => {
      if (h.to > h.from) decos.push(delMark.range(h.from, h.to))
      decos.push(Decoration.widget({ widget: new InsertWidget(h.insert, i), side: 1 }).range(h.to))
    })
    decos.push(Decoration.widget({ widget: new AllWidget(), side: 2 }).range(p.to))
  }
  return Decoration.set(decos, true)
}

const decorations = EditorView.decorations.compute([authorship, proposal, 'doc'], buildDecorations)

function toolbar(hooks: EditorHooks) {
  return StateField.define<Tooltip | null>({
    create: () => null,
    update(value, tr) {
      const sel = tr.state.selection.main
      if (sel.empty || tr.state.field(proposal)) return null
      // Keep the same tooltip (and its open form) unless the selection moved.
      if (value && !tr.docChanged && tr.startState.selection.main.eq(sel)) return value
      return {
        pos: sel.from,
        above: true,
        create: () => ({ dom: toolbarDom(hooks, sel.from, sel.to) }),
      }
    },
    provide: (f) => showTooltip.from(f),
  })
}

function toolbarDom(hooks: EditorHooks, from: number, to: number): HTMLElement {
  const dom = document.createElement('div')
  dom.className = 'sel-toolbar'
  const row = document.createElement('div')
  row.className = 'sel-actions'
  const form = document.createElement('form')
  form.className = 'sel-form'
  form.hidden = true
  const input = labelled(Object.assign(document.createElement('textarea'), { rows: 2 }))
  input.dataset.label = 'instruction'
  labelled(input)
  const submit = labelled(iconButton('send', 'submit', 'sel-submit'))
  submit.type = 'submit'
  form.append(input, submit)
  let mode: SelectionAction = 'ask'
  const open = (m: SelectionAction) => {
    mode = m
    input.value = ''
    form.hidden = false
    form.dataset.mode = m
    input.focus()
  }
  const ask = labelled(iconButton('ask', 'ask', 'sel-ask'))
  const rewrite = labelled(iconButton('rewrite', 'rewrite', 'sel-rewrite'))
  const change = labelled(iconButton('change', 'change', 'sel-change'))
  ask.addEventListener('click', () => { open('ask'); })
  change.addEventListener('click', () => { open('change'); })
  rewrite.addEventListener('click', () => { hooks.onAction('rewrite', from, to, ''); })
  const send = () => {
    const text = input.value.trim()
    if (!text) return
    input.value = ''
    form.hidden = true
    hooks.onAction(mode, from, to, text)
  }
  form.addEventListener('submit', (e) => {
    e.preventDefault()
    send()
  })
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  })
  row.append(ask, rewrite, change)
  dom.append(row, form)
  return dom
}

export const langCompartment = new Compartment()

export function langAttrs(lang: string) {
  return EditorView.contentAttributes.of({ lang, dir: 'auto', 'aria-label': label('editor') })
}

export function createEditor(parent: HTMLElement, hooks: EditorHooks, lang: string, draftId: string): EditorView {
  return new EditorView({
    parent,
    extensions: [
      history(),
      keymap.of([...defaultKeymap, ...historyKeymap]),
      markdown(),
      EditorView.lineWrapping,
      authorshipExtensions,
      decorations,
      imageDecorations(draftId),
      imageDropHandlers(hooks.onImages),
      toolbar(hooks),
      langCompartment.of(langAttrs(lang)),
      EditorView.updateListener.of((u) => {
        hooks.onUpdate(u)
      }),
    ],
  })
}
