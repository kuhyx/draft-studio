import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import {
  assetUrl,
  findImageRefs,
  handleDrop,
  handlePaste,
  imageDecorations,
  imageDropHandlers,
  imageFiles,
  ImageWidget,
  insertSpec,
  uploadAll,
} from './images'

const file = (name: string, type: string) => new File([new Uint8Array([1, 2, 3])], name, { type })

afterEach(() => {
  document.body.replaceChildren()
})

describe('findImageRefs', () => {
  it('finds asset references and the end of their line', () => {
    const text = 'Hi\n![a cat](assets/cat-1.png)\nmid ![](assets/b.jpg) and ![x](assets/c.gif)\nend'
    expect(findImageRefs(text)).toEqual([
      { alt: 'a cat', name: 'cat-1.png', lineEnd: 29, missing: false },
      { alt: '', name: 'b.jpg', lineEnd: 74, missing: false },
      { alt: 'x', name: 'c.gif', lineEnd: 74, missing: false },
    ])
  })

  it('ignores remote, data and non-asset paths', () => {
    const text = [
      '![](https://evil.example/x.png)',
      '![](data:image/png;base64,AAAA)',
      '![](/api/drafts/x/assets/a.png)',
      '[link](assets/a.png)',
    ].join('\n')
    expect(findImageRefs(text)).toEqual([])
  })

  it('handles a reference on the last line without a newline', () => {
    expect(findImageRefs('![z](assets/z.webp)')).toEqual([{ alt: 'z', name: 'z.webp', lineEnd: 19, missing: false }])
  })
})

describe('assetUrl', () => {
  it('points at the draft asset route, encoded', () => {
    expect(assetUrl('abc123def456', 'a.png')).toBe('/api/drafts/abc123def456/assets/a.png')
    expect(assetUrl('a/b', 'c d.png')).toBe('/api/drafts/a%2Fb/assets/c%20d.png')
  })
})

describe('insertSpec', () => {
  it('puts the markdown on its own line', () => {
    expect(insertSpec('ab', 1, ['![](assets/x.png)'])).toEqual({
      from: 1,
      insert: '\n![](assets/x.png)\n',
      cursor: 20,
    })
  })

  it('needs no extra newlines at line edges and walks past the next newline', () => {
    const doc = 'one\ntwo'
    expect(insertSpec(doc, 4, ['M'])).toEqual({ from: 4, insert: 'M\n', cursor: 6 })
    expect(insertSpec(doc, 3, ['M'])).toEqual({ from: 3, insert: '\nM', cursor: 6 })
    expect(insertSpec('', 0, ['A', 'B'])).toEqual({ from: 0, insert: 'A\nB\n', cursor: 4 })
  })

  it('clamps a stale position into the document', () => {
    expect(insertSpec('ab', 99, ['M']).from).toBe(2)
    expect(insertSpec('ab', -5, ['M']).from).toBe(0)
  })
})

describe('imageFiles', () => {
  it('keeps only the four supported image types', () => {
    const files = [file('a.png', 'image/png'), file('b.svg', 'image/svg+xml'), file('c.txt', 'text/plain')]
    expect(imageFiles(files).map((f) => f.name)).toEqual(['a.png'])
    expect(imageFiles([file('j', 'image/jpeg'), file('g', 'image/gif'), file('w', 'image/webp')])).toHaveLength(3)
    expect(imageFiles(null)).toEqual([])
    expect(imageFiles(undefined)).toEqual([])
  })
})

describe('uploadAll', () => {
  it('uploads in order and keeps going after a failure', async () => {
    const up = vi.fn((f: File) =>
      f.name === 'bad.png' ? Promise.reject(new ApiError('too-large')) : Promise.resolve({ name: f.name, md: `![](assets/${f.name})` }),
    )
    const out = await uploadAll([file('a.png', 'image/png'), file('bad.png', 'image/png'), file('c.png', 'image/png')], up)
    expect(out).toEqual({ mds: ['![](assets/a.png)', '![](assets/c.png)'], error: 'too-large' })
  })

  it('reports an unknown failure as server', async () => {
    const out = await uploadAll([file('a.png', 'image/png')], () => Promise.reject(new Error('x')))
    expect(out).toEqual({ mds: [], error: 'server' })
  })
})

function transfer(files: File[]) {
  return { files: files as unknown as FileList, getData: () => '' }
}

describe('paste and drop handlers', () => {
  const view = (pos: number | null) => ({
    state: { selection: { main: { head: 7 } } },
    posAtCoords: vi.fn(() => pos),
  })

  it('paste of image files uploads at the cursor', () => {
    const onImages = vi.fn()
    const e = { clipboardData: transfer([file('a.png', 'image/png')]), preventDefault: vi.fn() }
    expect(handlePaste(e, view(null), onImages)).toBe(true)
    expect(e.preventDefault).toHaveBeenCalled()
    expect(onImages).toHaveBeenCalledWith([expect.any(File)], 7, 'paste')
  })

  it('paste of plain text is left to CodeMirror', () => {
    const onImages = vi.fn()
    const e = { clipboardData: transfer([]), preventDefault: vi.fn() }
    expect(handlePaste(e, view(null), onImages)).toBe(false)
    expect(handlePaste({ clipboardData: null, preventDefault: vi.fn() }, view(null), onImages)).toBe(false)
    expect(e.preventDefault).not.toHaveBeenCalled()
    expect(onImages).not.toHaveBeenCalled()
  })

  it('drop uploads at the drop point, or the cursor when outside text', () => {
    const onImages = vi.fn()
    const mk = () => ({
      dataTransfer: transfer([file('a.png', 'image/png')]),
      preventDefault: vi.fn(),
      clientX: 10,
      clientY: 20,
    })
    const v = view(3)
    expect(handleDrop(mk(), v, onImages)).toBe(true)
    expect(v.posAtCoords).toHaveBeenCalledWith({ x: 10, y: 20 })
    expect(onImages).toHaveBeenLastCalledWith([expect.any(File)], 3, 'drop')
    expect(handleDrop(mk(), view(null), onImages)).toBe(true)
    expect(onImages).toHaveBeenLastCalledWith([expect.any(File)], 7, 'drop')
  })

  it('dropping only unsupported files is swallowed with an error, plain drags pass through', () => {
    const onImages = vi.fn()
    const e = { dataTransfer: transfer([file('x.svg', 'image/svg+xml')]), preventDefault: vi.fn(), clientX: 0, clientY: 0 }
    expect(handleDrop(e, view(1), onImages)).toBe(true)
    expect(e.preventDefault).toHaveBeenCalled()
    expect(onImages).toHaveBeenCalledWith([], 1, 'drop')
    const text = { dataTransfer: transfer([]), preventDefault: vi.fn(), clientX: 0, clientY: 0 }
    expect(handleDrop(text, view(1), onImages)).toBe(false)
    expect(handleDrop({ ...text, dataTransfer: null }, view(1), onImages)).toBe(false)
  })

  it('is wired to the editor DOM events', () => {
    const onImages = vi.fn()
    const view = new EditorView({ doc: 'abc', extensions: [imageDropHandlers(onImages)], parent: document.body })
    const fire = (type: string, key: string, files: File[]) => {
      const e = new Event(type, { bubbles: true, cancelable: true })
      Object.assign(e, { [key]: transfer(files), clientX: 0, clientY: 0 })
      view.contentDOM.dispatchEvent(e)
      return e
    }
    expect(fire('paste', 'clipboardData', [file('a.png', 'image/png')]).defaultPrevented).toBe(true)
    expect(onImages).toHaveBeenCalledWith([expect.any(File)], 0, 'paste')
    expect(fire('drop', 'dataTransfer', []).defaultPrevented).toBe(false)
    view.destroy()
  })
})

describe('ImageWidget', () => {
  it('renders the image scaled to the editor, with its alt', () => {
    const w = new ImageWidget('/api/drafts/d/assets/a.png', 'a cat')
    const view = { requestMeasure: vi.fn() }
    const dom = w.toDOM(view as unknown as EditorView)
    const img = dom.querySelector('img')
    expect(dom.className).toBe('cm-image')
    expect(img?.getAttribute('src')).toBe('/api/drafts/d/assets/a.png')
    expect(img?.alt).toBe('a cat')
    img?.dispatchEvent(new Event('load'))
    expect(view.requestMeasure).toHaveBeenCalled()
  })

  it('is equal by src and alt so typing does not reload it', () => {
    const a = new ImageWidget('/a', 'x')
    expect(a.eq(new ImageWidget('/a', 'x'))).toBe(true)
    expect(a.eq(new ImageWidget('/a', 'y'))).toBe(false)
    expect(a.eq(new ImageWidget('/b', 'x'))).toBe(false)
  })
})

describe('imageDecorations', () => {
  it('adds one block widget under each referencing line', () => {
    const state = EditorState.create({
      doc: 'top\n![a](assets/a.png)\ntext\n![](assets/b.png) ![](assets/c.png)',
      extensions: [imageDecorations('d1')],
    })
    const view = new EditorView({ state, parent: document.body })
    const found: { pos: number; src: string; block: boolean }[] = []
    for (const set of view.state.facet(EditorView.decorations)) {
      if (typeof set === 'function') continue
      set.between(0, view.state.doc.length, (from, _to, deco) => {
        const spec = deco.spec as { widget: ImageWidget; block?: boolean }
        found.push({ pos: from, src: spec.widget.src, block: spec.block === true })
      })
    }
    expect(found).toEqual([
      { pos: 22, src: '/api/drafts/d1/assets/a.png', block: true },
      { pos: 63, src: '/api/drafts/d1/assets/b.png', block: true },
      { pos: 63, src: '/api/drafts/d1/assets/c.png', block: true },
    ])
    view.destroy()
  })
})
