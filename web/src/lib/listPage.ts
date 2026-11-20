/** Drafts list: create (mail or text), open, move to trash. */
import { api, ApiError, type Kind, type ListItem } from './api'
import { initLocale, watchBuild } from './boot'
import { label } from './i18n'
import { icon } from './icons'
import { setStatus } from './status'

export async function bootList(): Promise<void> {
  const status = document.querySelector<HTMLElement>('#status')
  const list = document.querySelector<HTMLElement>('#drafts')
  if (!status || !list) return
  await initLocale(document, () => void refresh())
  const fail = (err: unknown) => {
    setStatus(status, 'error', err instanceof ApiError ? err.code : 'server')
  }
  const create = (kind: Kind) => {
    api
      .create(kind)
      .then((meta) => {
        location.href = `/d/?id=${meta.id}`
      })
      .catch(fail)
  }
  document.querySelector('#new-mail')?.addEventListener('click', () => {
    create('mail')
  })
  document.querySelector('#new-text')?.addEventListener('click', () => {
    create('text')
  })
  async function refresh(): Promise<void> {
    try {
      const drafts = await api.list()
      list?.replaceChildren(...drafts.map((d) => row(d, refresh, fail)))
      if (status) setStatus(status, 'saved')
    } catch (err) {
      fail(err)
    }
  }
  watchBuild(document)
  document.body.dataset.ready = '1'
}

function row(d: ListItem, refresh: () => Promise<void>, fail: (e: unknown) => void): HTMLElement {
  const li = document.createElement('li')
  li.className = 'draft-row'
  li.dataset.id = d.id
  li.dataset.kind = d.kind
  const open = document.createElement('a')
  open.href = `/d/?id=${d.id}`
  open.className = 'draft-open'
  open.setAttribute('aria-label', label('open'))
  open.innerHTML = icon(d.kind)
  const preview = document.createElement('span')
  preview.className = 'preview'
  preview.textContent = d.kind === 'mail' && d.subject ? d.subject : d.preview || d.brief
  if (d.lang) preview.lang = d.lang
  preview.dir = 'auto'
  open.append(preview)
  const time = document.createElement('time')
  time.textContent = new Date(d.updated * 1000).toLocaleString()
  const del = document.createElement('button')
  del.type = 'button'
  del.className = 'icon-btn delete'
  del.innerHTML = icon('trash')
  del.dataset.label = 'delete'
  del.setAttribute('aria-label', label('delete'))
  del.addEventListener('click', () => {
    api
      .remove(d.id)
      .then(refresh)
      .catch(fail)
  })
  li.append(open, time, del)
  return li
}
