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
 * - A missing image never shows as a picture (kuhy: "fake images are worse than
 *   no image at all"): a name the server can never store draws a red dashed
 *   frame with a glyph at once, and a stored-looking name that fails to load
 *   swaps to the same frame. The <img> stays hidden until it loads, so a broken
 *   picture or its alt text never flashes. Accessible name: the one-word
 *   `tips.missingImage` via label()'s fallback; no visible text.
 */
import type { EditorState, Range as CMRange } from '@codemirror/state'
import { Decoration, type DecorationSet, EditorView, WidgetType } from '@codemirror/view'
import { ApiError } from './api'
import { label } from './i18n'
import { icon } from './icons'

/** Every image reference under assets/; only SAFE_NAME names can exist on the server. */
const REF_RE = /!\[([^\]\n]*)\]\(assets\/([^)\n]*)\)/g
/** Same charset the server stores (draft_studio.store.ASSET_NAME_RE). */
const SAFE_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*\.(?:png|jpe?g|gif|webp)$/
export const IMAGE_TYPES: readonly string[] = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']

export interface ImageRef {
  alt: string
  name: string
  /** Offset of the end of the line holding the reference (widget position). */
  lineEnd: number
  /** The server can never hold this name (draft_studio.links counts it missing too). */
  missing: boolean
}

export function findImageRefs(text: string): ImageRef[] {
  const out: ImageRef[] = []
  for (const m of text.matchAll(REF_RE)) {
    const nl = text.indexOf('\n', m.index)
    out.push({ alt: m[1], name: m[2], lineEnd: nl === -1 ? text.length : nl, missing: !SAFE_NAME.test(m[2]) })
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

/** The missing-image frame: glyph only, named for screen readers by the tip word. */
function missingMarker(box: HTMLElement): HTMLElement {
  const marker = document.createElement('span')
  marker.className = 'cm-image-broken'
  marker.setAttribute('role', 'img')
  marker.setAttribute('aria-label', label('missingImage'))
  marker.innerHTML = icon('imageOff')
  box.dataset.broken = '1'
  return marker
}

export class ImageWidget extends WidgetType {
  constructor(
    readonly src: string,
    readonly alt: string,
    readonly missing = false,
  ) {
    super()
  }
  override eq(other: ImageWidget): boolean {
    return other.src === this.src && other.alt === this.alt && other.missing === this.missing
  }
  toDOM(view: EditorView): HTMLElement {
    const box = document.createElement('div')
    box.className = 'cm-image'
    if (this.missing) {
      box.append(missingMarker(box))
      return box
    }
    const img = document.createElement('img')
    img.src = this.src
    img.alt = this.alt
    img.draggable = false
    img.hidden = true
    img.addEventListener('load', () => {
      img.hidden = false
      view.requestMeasure()
    })
    img.addEventListener('error', () => {
      img.replaceWith(missingMarker(box))
      view.requestMeasure()
    })
    box.append(img)
    return box
  }
}

function build(state: EditorState, draftId: string): DecorationSet {
  const decos: CMRange<Decoration>[] = findImageRefs(state.doc.toString()).map((r) =>
    Decoration.widget({ widget: new ImageWidget(assetUrl(draftId, r.name), r.alt, r.missing), block: true, side: 1 }).range(r.lineEnd),
  )
  return Decoration.set(decos, true)
}

/** Block widgets for every image reference of this draft. */
export function imageDecorations(draftId: string) {
  return EditorView.decorations.compute(['doc'], (state) => build(state, draftId))
}
