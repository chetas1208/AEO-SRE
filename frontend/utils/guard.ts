// Presentation helpers for Change Guard. The server decides; these only map its decision to labels and to which
// controls are enabled. Nothing here evaluates overlap, contradiction, or eligibility (docs/CHANGE_GUARD_SPEC.md).
import type { ChangeCheck, GuardDecision } from '~/types'

export interface DecisionMeta { label: string; tone: 'good' | 'info' | 'warn' | 'bad' | 'muted'; glyph: string; summary: string }

// Colour semantics follow UI.md 43 (green positive, blue primary, amber pending/warning, red failure, grey unavailable).
// The text label and glyph are always rendered so colour is never the only signal.
const META: Record<GuardDecision, DecisionMeta> = {
  ALLOW: { label: 'ALLOW', tone: 'good', glyph: '✓', summary: 'No conflicts found' },
  MERGE: { label: 'MERGE', tone: 'info', glyph: '+', summary: 'Duplicates another change; a merged proposal is suggested' },
  DELAY: { label: 'DELAY', tone: 'warn', glyph: '‖', summary: 'Held until a protected experiment finishes measuring' },
  REQUIRE_REVIEW: { label: 'REQUIRE REVIEW', tone: 'warn', glyph: '!', summary: 'A human must review before this proceeds' },
  BLOCK: { label: 'BLOCK', tone: 'bad', glyph: '✕', summary: 'Blocked by the guard' }
}

export function decisionMeta(d?: GuardDecision | null): DecisionMeta {
  return (d && META[d]) || { label: 'Unavailable', tone: 'muted', glyph: '?', summary: 'Guard decision unavailable' }
}

export interface ApproveGate {
  /** false => the Approve control must be disabled. */
  allowed: boolean
  /** true => a typed review reason is required and sent as `review_reason`. */
  needsReason: boolean
  /** Explanation shown next to a disabled / warned control. */
  message: string | null
  eligibleAfter: string | null
  /** true when there is no verdict (unavailable): approve is not blocked client-side, the server still enforces. */
  unavailable: boolean
}

/** Approve gating from the server verdict. BLOCK/DELAY disable; REQUIRE_REVIEW needs a non-empty reason. */
export function approveGate(guard: ChangeCheck | null | undefined, reviewReason = ''): ApproveGate {
  const d = guard?.decision ?? null
  const eligibleAfter = guard?.eligibleAfter ?? guard?.findings?.find((f) => f.eligibleAfter)?.eligibleAfter ?? null
  const reason = guard?.findings?.find((f) => f.reason)?.reason ?? null
  if (d === 'BLOCK') return { allowed: false, needsReason: false, unavailable: false, eligibleAfter, message: reason ?? 'Blocked by the change guard.' }
  if (d === 'DELAY') return { allowed: false, needsReason: false, unavailable: false, eligibleAfter, message: reason ?? 'Delayed by the change guard.' }
  if (d === 'REQUIRE_REVIEW') {
    const ok = reviewReason.trim().length > 0
    return { allowed: ok, needsReason: true, unavailable: false, eligibleAfter, message: ok ? null : 'Enter a review reason to approve.' }
  }
  if (d === null) return { allowed: true, needsReason: false, unavailable: true, eligibleAfter: null, message: 'Guard verdict unavailable. The server still enforces the guard when you approve.' }
  return { allowed: true, needsReason: false, unavailable: false, eligibleAfter, message: null }
}

export function semanticCheckLabel(state?: string | null): string {
  switch (state) {
    case null: case undefined: case '': return 'Semantic check: unavailable'
    case 'skipped_no_canonical_truth': return 'Semantic check skipped: no canonical truth defined for this organization'
    case 'degraded': return 'Semantic check degraded: deterministic rules only, no model/ranker pass was run'
    case 'ran': case 'ok': case 'full': case 'ready': return 'Semantic check ran'
    default: return `Semantic check: ${state.replace(/_/g, ' ')}`
  }
}

/** True only when the server says the semantic check actually ran. Used so "ALLOW" never reads as a full pass otherwise. */
export function semanticRan(state?: string | null): boolean {
  return state === 'ran' || state === 'ok' || state === 'full' || state === 'ready'
}

export function pct(v?: number | null): string {
  return v === null || v === undefined || Number.isNaN(v) ? 'unavailable' : `${Math.round(v * 10) / 10}%`
}

/** Map the guard's typed API errors to operator copy; undefined for everything else. */
export function guardErrorMessage(code?: string | null): string | undefined {
  if (code === 'CHANGE_GUARD_BLOCKED') return 'Blocked by the change guard. The server refused this approval; see the guard verdict above.'
  if (code === 'APPROVAL_DIGEST_MISMATCH') return 'The proposal changed since approval; re-approve.'
  return undefined
}
