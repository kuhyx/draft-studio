/**
 * Which drafts changed on the server (GET /api/events, draft_studio/feed.py).
 * One tab per browser holds the stream (a Web Lock) and passes each id on over a
 * BroadcastChannel: a stream per tab would take every HTTP/1.1 connection the
 * browser allows per host and starve autosave. When the leader tab closes, the
 * lock passes to another. `*` (stream (re)opened): anything may have changed.
 */
import { z } from 'zod'

const NAME = 'draft-studio-feed'
const eventSchema = z.object({ id: z.string() })

export interface FeedDeps {
  /** Open the stream: `opened` on each (re)connect, `data` per event line. */
  open: (opened: () => void, data: (raw: string) => void) => void
  channel: { listen: (cb: (data: unknown) => void) => void; post: (m: unknown) => void }
  locks?: { request: (name: string, cb: () => Promise<void>) => Promise<unknown> } | undefined
}

const idOf = (data: unknown): string | null => {
  const parsed = eventSchema.safeParse(data)
  return parsed.success ? parsed.data.id : null
}

const parse = (raw: string): unknown => {
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

function browserDeps(): FeedDeps {
  const channel = new BroadcastChannel(NAME)
  return {
    open: (opened, data) => {
      const src = new EventSource('/api/events')
      src.onopen = opened
      src.onmessage = (e: MessageEvent<string>) => {
        data(e.data)
      }
    },
    channel: {
      listen: (cb) => {
        channel.onmessage = (e) => {
          cb(e.data)
        }
      },
      post: (m) => {
        channel.postMessage(m)
      },
    },
    locks: navigator.locks,
  }
}

/** Call `onChange(id)` for every draft another tab (or this one) changed. */
export function watchDrafts(onChange: (id: string) => void, deps: FeedDeps = browserDeps()): void {
  deps.channel.listen((data) => {
    const id = idOf(data)
    if (id) onChange(id)
  })
  const emit = (id: string) => {
    onChange(id)
    deps.channel.post({ id })
  }
  // Held for the tab's life: the promise never settles, the lock goes when the tab does.
  const lead = () =>
    new Promise<void>(() => {
      deps.open(
        () => {
          emit('*')
        },
        (raw) => {
          const id = idOf(parse(raw))
          if (id) emit(id)
        },
      )
    })
  if (deps.locks) void deps.locks.request(NAME, lead)
  else void lead()
}
