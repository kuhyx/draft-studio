import { describe, expect, it, vi } from 'vitest'
import { registerWorker } from './worker'

const fake = (controller: unknown, register = vi.fn(() => Promise.resolve({}))) => {
  let change = (): void => undefined
  const container = {
    controller,
    register,
    addEventListener: (_: string, cb: () => void) => {
      change = cb
    },
  }
  return {
    container: container as unknown as ServiceWorkerContainer,
    register,
    change: () => {
      change()
    },
  }
}
const failing = () => vi.fn(() => Promise.reject(new Error('blocked')))
const nothing = () => Promise.resolve()

describe('registerWorker', () => {
  it('registers the worker file at the root', () => {
    const f = fake(null)
    registerWorker(f.container, nothing)
    expect(f.register).toHaveBeenCalledWith('/sw.js')
  })

  it('reloads when a new worker takes over a controlled page', () => {
    const reload = vi.fn(nothing)
    const f = fake({})
    registerWorker(f.container, reload)
    f.change()
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('does not reload when the first worker claims a page that had none', () => {
    const reload = vi.fn(nothing)
    const f = fake(null)
    registerWorker(f.container, reload)
    f.change()
    expect(reload).not.toHaveBeenCalled()
  })

  it('reports a failed registration', async () => {
    const onError = vi.fn()
    registerWorker(fake(null, failing()).container, nothing, onError)
    await vi.waitFor(() => {
      expect(onError).toHaveBeenCalledTimes(1)
    })
  })

  it('logs a failed registration by default', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    registerWorker(fake(null, failing()).container, nothing)
    await vi.waitFor(() => {
      expect(log).toHaveBeenCalledTimes(1)
    })
    log.mockRestore()
  })

  it('does nothing without service worker support', () => {
    expect(() => {
      registerWorker(undefined, nothing)
    }).not.toThrow()
  })
})
