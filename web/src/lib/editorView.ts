/**
 * The CodeMirror view: markdown editing, Claude tint, the selection toolbar
 * and the inline word diff with per-hunk accept/reject.
 */
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { markdown } from '@codemirror/lang-markdown'
import { Compartment, StateField } from '@codemirror/state'
import { EditorView, keymap, showTooltip, type Tooltip, type ViewUpdate } from '@codemirror/view'
import { decorations, labelled } from './diffView'
import { authorshipExtensions, type Resolver, resolver } from './editorState'
import { label } from './i18n'
import { heldSpans } from './heldSpans'
import { iconButton } from './icons'
import { imageDecorations, imageDropHandlers, type OnImages } from './images'
import { signalExtensions } from './signals'

export type SelectionAction = 'ask' | 'rewrite' | 'change'
export interface EditorHooks {
  /** Any editor update (autosave, stale-request tracking). */
  onUpdate: (u: ViewUpdate) => void
  /** Selection toolbar action on [from, to); instruction empty for rewrite. */
  onAction: (action: SelectionAction, from: number, to: number, instruction: string) => void
  /** Image files pasted or dropped at pos (empty: only unsupported files). */
  onImages: OnImages
  /** Accept or reject clicked on pending hunks: the server applies it. */
  onResolve: Resolver
}

function toolbar(hooks: EditorHooks) {
  return StateField.define<Tooltip | null>({
    create: () => null,
    update(value, tr) {
      const sel = tr.state.selection.main
      // Open inside a pending proposal too: a comment there revises it (editorAsks.ts).
      if (sel.empty) return null
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
      resolver.of(hooks.onResolve),
      decorations,
      signalExtensions,
      heldSpans,
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
