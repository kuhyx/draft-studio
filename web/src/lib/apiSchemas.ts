/** The server's response shapes (Zod) and their types; the client is api.ts. */
import { z } from 'zod'

export const rangeSchema = z.tuple([z.number().int(), z.number().int()])
export const askSchema = z.object({
  from: z.number().int(),
  to: z.number().int(),
  span: z.string(),
  question: z.string(),
  answer: z.string(),
  ts: z.number(),
})
export const authorSchema = z.enum(['you', 'claude'])
export const metaSchema = z.object({
  id: z.string(),
  kind: z.enum(['mail', 'text']),
  lang: z.string(),
  to: z.string(),
  subject: z.string(),
  toAuthor: authorSchema,
  subjectAuthor: authorSchema,
  gmailDraftId: z.string(),
  brief: z.string(),
  intake: z.array(z.object({ q: z.string(), a: z.string().nullable(), round: z.number().int() })),
  phase: z.enum(['brief', 'intake', 'editing', 'approved']),
  approvedSha256: z.string().optional(),
  asks: z.array(askSchema),
  claude: z.array(rangeSchema),
  version: z.number().int(),
  updated: z.number(),
})
/** A pending proposal the server holds for the draft (draft_studio/proposals.py): positions on the saved text. */
export const pendingSchema = z.object({
  id: z.number().int(),
  rev: z.number().int(),
  from: z.number().int(),
  to: z.number().int(),
  replacement: z.string(),
  /** Screenshots Claude asked for and did not get (keys): the proposal shows an image-off glyph. */
  missing: z.array(z.string()).default([]),
})
/**
 * `approved`: the server re-checked the stored hash against draft.md on disk. `proposals`: Claude's pending
 * proposals; `gone`: how the last ones ended (stale: an edit touched them; accepted; rejected).
 */
export const stateSchema = z.object({
  meta: metaSchema,
  text: z.string(),
  claude: z.array(rangeSchema),
  approved: z.boolean(),
  proposals: z.array(pendingSchema).default([]),
  gone: z.record(z.string(), z.string()).default({}),
})
export const listItemSchema = metaSchema.extend({ preview: z.string() })
export const versionSchema = z.object({
  v: z.number().int().optional(),
  ts: z.number(),
  type: z.string(),
  text: z.string().optional(),
  claude: z.array(rangeSchema).optional(),
  question: z.string().optional(),
  answer: z.string().optional(),
})
/** `stripped`: image links Claude made up that the server cut from its answer. */
export const strippedSchema = z.array(z.string()).default([])
/** `dropped`: Claude's screenshots that could not be taken; their lines were cut (capture/__init__.py). */
export const droppedSchema = z.array(z.object({ key: z.string(), reason: z.string() })).default([])
export const stepSchema = z.object({
  questions: z.array(z.string()),
  state: stateSchema,
  stripped: strippedSchema,
  dropped: droppedSchema,
})
export const uploadSchema = z.object({ name: z.string(), md: z.string() })
/** How the server landed it: ok; stale (your edit touched the span); gone (a revised one vanished); overlap. */
export const outcomeSchema = z.enum(['ok', 'stale', 'gone', 'overlap']).default('ok')
export const proposalSchema = z.object({
  replacement: z.string(),
  from: z.number().int(),
  to: z.number().int(),
  outcome: outcomeSchema,
  stripped: strippedSchema,
  dropped: droppedSchema,
  /** Some screenshot is missing (`dropped` says which). */
  partial: z.boolean().default(false),
})
/** A capture refusal kuhy grants or denies; loose: the permission card reads the other fields too. */
export const requestSchema = z.looseObject({
  id: z.string(),
  reason: z.string(),
  detail: z.string(),
  shot: z.string(),
  /** The (method, url) pairs the click sent and the guard held back: one card row each (grantPick.ts). */
  blocked: z.array(z.tuple([z.string(), z.string()])).optional().catch(undefined),
  /** Aligned with `blocked`: the method an OPTIONS preflight announces, else null; absent on older servers. */
  announces: z.array(z.string().nullable()).optional().catch(undefined),
})
/** An answer the server holds until a capture gets its permission, login or free profile. */
export const heldSchema = z.object({
  id: z.string(),
  kind: z.enum(['proposal', 'draft']),
  from: z.number().int().nullable(),
  to: z.number().int().nullable(),
  requests: z.array(requestSchema),
  login: z.array(z.string()),
  busy: z.boolean(),
  /** Retries run so far: moves even when a retry fails the same way. */
  tries: z.number().int().default(0),
  /** A proposal none of whose screenshots could be taken: held, not released; retry or dismiss. */
  failed: droppedSchema,
})
export const heldReplySchema = z.object({ held: heldSchema })
export const releasedSchema = z.discriminatedUnion('kind', [
  proposalSchema.extend({ id: z.string(), kind: z.literal('proposal'), span: z.string(), previous: z.string().nullable() }),
  stepSchema.extend({ id: z.string(), kind: z.literal('draft') }),
])
export const listingSchema = z.object({ held: z.array(heldSchema), released: z.array(releasedSchema) })
export const gmailSchema = z.object({ id: z.string(), created: z.boolean() })
export const errorSchema = z.object({ error: z.string(), state: stateSchema.optional() })

export type Meta = z.infer<typeof metaSchema>
export type DraftState = z.infer<typeof stateSchema>
export type Ask = z.infer<typeof askSchema>
export type Version = z.infer<typeof versionSchema>
export type ListItem = z.infer<typeof listItemSchema>
export type Step = z.infer<typeof stepSchema>
export type Proposal = z.infer<typeof proposalSchema>
export type PendingProposal = z.infer<typeof pendingSchema>
export type CaptureRequest = z.infer<typeof requestSchema>
export type Held = z.infer<typeof heldSchema>
export type HeldReply = z.infer<typeof heldReplySchema>
export type HeldListing = z.infer<typeof listingSchema>
export type Released = HeldListing['released'][number]
export type ReleasedProposal = Extract<Released, { kind: 'proposal' }>
export type ReleasedDraft = Extract<Released, { kind: 'draft' }>
export type Kind = Meta['kind']
export type Phase = Meta['phase']
export type Author = z.infer<typeof authorSchema>
