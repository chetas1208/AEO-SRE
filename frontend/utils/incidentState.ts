import type { ExperimentList, IncidentDetail, IncidentList, IncidentState } from '~/types'

const ACTIONABLE: IncidentState[] = [
  'detected', 'triaged', 'investigating', 'evidence_ready', 'root_cause_proposed', 'root_cause_confirmed',
  'intervention_proposed', 'awaiting_approval', 'approved', 'failed'
]

/** Attention badge: API-provided count when present; otherwise a plain count of unresolved actionable states. */
const ATTENTION_STATUS = ['detected', 'investigating', 'needs_review', 'ready_for_action', 'failed']

export function attentionCount(list?: IncidentList | null): number | null {
  if (!list) return null
  const by = list.counts.byStatus
  if (Object.keys(by).length) return ATTENTION_STATUS.reduce((n, k) => n + (by[k] ?? 0), 0)
  return list.items.filter((i) => ACTIONABLE.includes(i.state)).length
}

export function waitingCount(list?: ExperimentList | null): number | null {
  if (!list) return null
  if (list.counts?.awaitingMeasurement != null) return list.counts.awaitingMeasurement
  return list.items.filter((e) => e.status === 'awaiting_verification' || e.status === 'executed').length
}

export type CtaKind = 'investigate' | 'review' | 'approve' | 'execute' | 'mark_executed' | 'resolve' | 'none'
export interface Cta { kind: CtaKind; label: string; disabled: boolean; hint?: string; interventionId?: string | null; targetTab?: string | null }

/** UI.md §12/§26: incident state decides the single primary CTA. */
export function primaryCta(state: IncidentState | string, hasIntervention: boolean): Cta {
  switch (state) {
    case 'detected': case 'triaged': return { kind: 'investigate', label: 'Investigate', disabled: false }
    case 'failed': return { kind: 'investigate', label: 'Re-run investigation', disabled: false }
    case 'investigating': return { kind: 'none', label: 'Investigation running', disabled: true }
    case 'evidence_ready': case 'root_cause_proposed': case 'root_cause_confirmed':
      return hasIntervention ? { kind: 'review', label: 'Review recommendation', disabled: false } : { kind: 'none', label: 'Computing intervention policy…', disabled: true }
    case 'intervention_proposed': case 'awaiting_approval':
      return { kind: 'approve', label: 'Approve', disabled: !hasIntervention, hint: hasIntervention ? undefined : 'No candidate intervention available' }
    case 'approved': return { kind: 'mark_executed', label: 'Mark as executed', disabled: !hasIntervention, hint: 'Apply the intervention package, then record when you did' }
    case 'executing': return { kind: 'none', label: 'Executing…', disabled: true }
    case 'executed': case 'awaiting_verification': return { kind: 'none', label: 'Awaiting Measurement', disabled: true, hint: 'Awaiting post-intervention observation' }
    case 'verified': case 'rewarded': return { kind: 'resolve', label: 'Resolve', disabled: false }
    default: return { kind: 'none', label: stateLabel(state), disabled: true }
  }
}

const BACKEND_CTA: Record<string, CtaKind> = { investigate: 'investigate', review: 'review', approve: 'approve', approve_execute: 'approve', execute: 'execute', mark_executed: 'mark_executed', resolve: 'resolve', none: 'none' }

/** The backend computes the primary CTA (UI.md §12); fall back to the local state map only if it is absent. */
export function resolveCta(inc: IncidentDetail, hasIntervention: boolean): Cta {
  const b = inc.primaryCta
  if (!b) return primaryCta(inc.state, hasIntervention)
  const kind = BACKEND_CTA[b.key] ?? 'none'
  return { kind, label: b.label, disabled: !b.enabled || kind === 'none', hint: b.reason ?? undefined, interventionId: b.interventionId, targetTab: b.targetTab }
}
