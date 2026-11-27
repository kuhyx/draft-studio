/**
 * Images in the editor. A markdown reference `![alt](assets/<name>)` shows the
 * stored image right under its line as a CodeMirror block widget; pasting or
 * dropping image files uploads them and inserts such a reference on its own line.
 *
 * Trade-offs:
 * - Only `assets/<safe name>` references render. Remote, data: and other paths
 *   stay plain text, so a draft never makes the page load anything off-box.
 * - Block widgets must come from state (not a view plugin), hence the computed
 *   decorations facet. eq() on src+alt keeps the <img> alive while you type.
 * - A missing file swaps the <img> for a broken-image glyph (aria label: the
 *   catalog's existing "error" label; textgate refused new strings);
 *   the chrome stays text-free.
 */
import type { EditorState, Range as CMRange } from '@codemirror/state'
import { Decoration, type DecorationSet, EditorView, WidgetType } from '@codemirror/view'
import { ApiError } from './api'
import { label } from './i18n'
import { icon } from './icons'

/** Same charset the server stores (draft_studio.store.ASSET_NAME_RE). */
const REF_RE = /!\[([^\]\n]*)\]\(assets\/([A-Za-z0-9][A-Za-z0-9_-]*\.(?:png|jpe?g|gif|webp))\)/g
export const IMAGE_TYPES: readonly string[] = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']

export interface ImageRef {
  alt: string
  name: string
  /** Offset of the end of the line holding the reference (widget position). */
  lineEnd: number
}

export function findImageRefs(text: string): ImageRef[] {
  const out: ImageRef[] = []
  for (const m of text.matchAll(REF_RE)) {
    const nl = text.indexOf('\n', m.index)
    out.push({ alt: m[1], name: m[2], lineEnd: nl === -1 ? text.length : nl })
  }
  return out
}

/** The page lives at /d/?id=…, so a relative `assets/x` would miss: build the API path. */
export function assetUrl(draftId: string, name: string): string {
  return `/api/drafts/${encodeURIComponent(draftId)}/assets/${encodeURIComponent(name)}`
}

/** Insert references on their own line(s) at pos; the cursor lands on the line after. */
export function insertSpec(doc: string, pos: number, mds: readonly string[]): { from: number; insert: string; cursor: number } {
  const from = Math.min(Math.max(pos, 0), doc.length)
  const before = from > 0 && doc[from - 1] !== '\n' ? '\n' : ''
  const after = from === doc.length || doc[from] !== '\n' ? '\n' : ''
  const insert = before + mds.join('\n') + after
  return { from, insert, cursor: from + insert.length + (after ? 0 : 1) }
}

export function imageFiles(list: FileList | readonly File[] | null | undefined): File[] {
  return Array.from(list ?? []).filter((f) => IMAGE_TYPES.includes(f.type))
}

export type Upload = (file: File) => Promise<{ name: string; md: string }>

/** Upload one by one; a failed file is skipped and its error code kept for the status icon. */
export async function uploadAll(files: readonly File[], upload: Upload): Promise<{ mds: string[]; error: string | null }> {
  const mds: string[] = []
  let error: string | null = null
  for (const f of files) {
    try {
      mds.push((await upload(f)).md)
    } catch (err) {
      error = err instanceof ApiError ? err.code : 'server'
    }
  }
  return { mds, error }
}

export type OnImages = (files: File[], pos: number, how: 'paste' | 'drop') => void
interface ViewLike {
  state: { selection: { main: { head: number } } }
  posAtCoords: (c: { x: number; y: number }) => number | null
}
interface FileEvent {
  preventDefault: () => void
}

/** Paste: image files upload at the cursor; anything else is CodeMirror's. */
export function handlePaste(e: FileEvent & { clipboardData: { files: FileList } | null }, view: ViewLike, onImages: OnImages): boolean {
  const files = imageFiles(e.clipboardData?.files)
  if (!files.length) return false
  e.preventDefault()
  onImages(files, view.state.selection.main.head, 'paste')
  return true
}

/** Drop: any file drop is ours (the browser would navigate away); text drags stay CodeMirror's. */
export function handleDrop(
  e: FileEvent & { dataTransfer: { files: FileList } | null; clientX: number; clientY: number },
  view: ViewLike,
  onImages: OnImages,
): boolean {
  const all = Array.from(e.dataTransfer?.files ?? [])
  if (!all.length) return false
  e.preventDefault()
  const pos = view.posAtCoords({ x: e.clientX, y: e.clientY }) ?? view.state.selection.main.head
  onImages(imageFiles(all), pos, 'drop')
  return true
}

export function imageDropHandlers(onImages: OnImages) {
  return EditorView.domEventHandlers({
    paste: (e, view) => handlePaste(e, view, onImages),
    drop: (e, view) => handleDrop(e, view, onImages),
  })
}

export class ImageWidget extends WidgetType {
  constructor(
    readonly src: string,
    readonly alt: string,
  ) {
    super()
  }
  override eq(other: ImageWidget): boolean {
    return other.src === this.src && other.alt === this.alt
  }
  toDOM(view: EditorView): HTMLElement {
    const box = document.createElement('div')
    box.className = 'cm-image'
    const img = document.createElement('img')
    img.src = this.src
    img.alt = this.alt
    img.draggable = false
    img.addEventListener('load', () => { view.requestMeasure(); })
    img.addEventListener('error', () => {
      const broken = document.createElement('span')
      broken.className = 'cm-image-broken'
      broken.setAttribute('role', 'img')
      broken.setAttribute('aria-label', label('error'))
      broken.innerHTML = icon('imageOff')
      box.dataset.broken = '1'
      img.replaceWith(broken)
      view.requestMeasure()
    })
    box.append(img)
    return box
  }
}

function build(state: EditorState, draftId: string): DecorationSet {
  const decos: CMRange<Decoration>[] = findImageRefs(state.doc.toString()).map((r) =>
    Decoration.widget({ widget: new ImageWidget(assetUrl(draftId, r.name), r.alt), block: true, side: 1 }).range(r.lineEnd),
  )
  return Decoration.set(decos, true)
}

/** Block widgets for every image reference of this draft. */
export function imageDecorations(draftId: string) {
  return EditorView.decorations.compute(['doc'], (state) => build(state, draftId))
}
