import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from './api'

const meta = {
  id: 'abc123def456',
  kind: 'mail',
  lang: 'en',
  to: '',
  subject: '',
  toAuthor: 'you',
  subjectAuthor: 'claude',
  gmailDraftId: '',
  brief: '',
  intake: [],
  phase: 'brief',
  asks: [],
  claude: [],
  version: 0,
  updated: 1,
}
const state = { meta, text: '', claude: [] }

function reply(body: unknown, status = 200) {
  const fn = vi.fn((_url: string, _init?: RequestInit) =>
    Promise.resolve(new Response(typeof body === 'string' ? body : JSON.stringify(body), { status })),
  )
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api client', () => {
  it('parses every route', async () => {
    reply({ drafts: [{ ...meta, preview: 'p' }] })
    expect(await api.list()).toHaveLength(1)
    const f = reply(meta)
    expect((await api.create('mail')).id).toBe(meta.id)
    expect(f.mock.calls[0]?.[1]?.method).toBe('POST')
    reply({ ok: true })
    expect((await api.remove('x')).ok).toBe(true)
    reply(state)
    expect((await api.get('x')).text).toBe('')
    const save = reply(state)
    await api.save('x', { text: 'a' })
    expect(save.mock.calls[0]?.[1]?.keepalive).toBe(true)
    reply({ versions: [{ v: 0, ts: 1, type: 'create', text: '', claude: [] }] })
    expect(await api.history('x')).toHaveLength(1)
    reply(state)
    expect((await api.restore('x', 0)).text).toBe('')
    reply({ questions: ['q'], state })
    expect((await api.brief('x', 'b')).questions).toEqual(['q'])
    reply({ questions: [], state })
    expect((await api.intake('x', ['a'])).questions).toEqual([])
    reply({ from: 0, to: 1, span: 'a', question: 'q', answer: 'A', ts: 1 })
    expect((await api.ask('x', 'a', 0, 1, 'q')).answer).toBe('A')
    reply({ replacement: 'r', from: 0, to: 1 })
    expect((await api.propose('x', 'a', 0, 1, '')).replacement).toBe('r')
    reply({ id: 'g', created: true })
    expect((await api.gmail('x')).created).toBe(true)
    reply({ locales: ['en'] })
    expect(await api.locales()).toEqual(['en'])
    reply({ build: 'b' })
    expect(await api.version()).toBe('b')
  })

  it('maps failures to error codes', async () => {
    reply({ error: 'claude' }, 502)
    await expect(api.get('x')).rejects.toEqual(new ApiError('claude'))
    reply('not json', 500)
    await expect(api.get('x')).rejects.toEqual(new ApiError('server'))
    reply({ nope: 1 })
    await expect(api.get('x')).rejects.toEqual(new ApiError('bad-response'))
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('down'))))
    await expect(api.get('x')).rejects.toEqual(new ApiError('offline'))
  })
})
