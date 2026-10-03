import type { IncidentState, Severity, StepStatus, ExperimentStatus } from '~/types'

export function relativeTime(iso?: string | null, now: number = Date.now()): string {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 48) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

export function absoluteTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short'
  })
}

export function clockTime(iso?: string | null): string {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--:--:--'
  return d.toLocaleTimeString(undefined, { hour12: false })
}

export function shortDate(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/**
 * Units from the backend: 'pp' = value is a percentage (0-100) and its delta is in percentage points,
 * 'fraction' = 0-1 ratio (expected outcome), 'count' / 'pos' = plain numbers, 'relative' = percent change.
 */
export function formatValue(v?: number | null, unit?: string | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  if (unit === 'fraction') return `${trim(v * 100)}%`
  if (unit === 'pp' || unit === '%' || unit === 'percent') return `${trim(v)}%`
  if (unit === 'count' || unit === 'pos' || !unit) return Math.abs(v) >= 10000 ? compact(v) : trim(v)
  return `${trim(v)} ${unit}`
}

export function formatDeltaValue(d?: number | null, unit?: string | null): string {
  if (d === null || d === undefined || Number.isNaN(d)) return ''
  const sign = d > 0 ? '+' : d < 0 ? '−' : ''
  const abs = Math.abs(d)
  if (unit === 'fraction') return `${sign}${trim(abs * 100)}pp`
  if (unit === 'pp' || unit === '%' || unit === 'percent') return `${sign}${trim(abs)}pp`
  if (unit === 'relative') return `${sign}${trim(abs)}%`
  if (unit === 'count' || unit === 'pos' || !unit) return `${sign}${trim(abs)}`
  return `${sign}${trim(abs)} ${unit}`
}

export function trim(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100)
}

export function compact(n: number): string {
  return new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n)
}

export function signed(n?: number | null, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const s = n.toFixed(digits)
  return n > 0 ? `+${s}` : n < 0 ? s.replace('-', '−') : s
}

export function confidenceLabel(c?: number | null): string {
  if (c === null || c === undefined) return 'Unknown confidence'
  if (c >= 0.75) return 'High confidence'
  if (c >= 0.5) return 'Medium confidence'
  return 'Low confidence'
}

export function humanize(s?: string | null): string {
  if (!s) return ''
  const t = s.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').trim()
  return t.charAt(0).toUpperCase() + t.slice(1)
}

export const SEVERITY_LABEL: Record<Severity, string> = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' }

/** Display labels for UI.md §26 lifecycle states. Pure label mapping of the backend state; no inference. */
export function stateLabel(s?: IncidentState | string | null): string {
  switch (s) {
    case 'detected': case 'triaged': return 'Detected'
    case 'investigating': return 'Investigating'
    case 'evidence_ready': case 'root_cause_proposed': case 'root_cause_confirmed': return 'Needs Review'
    case 'intervention_proposed': case 'awaiting_approval': case 'approved': return 'Ready for Action'
    case 'executing': return 'Executing'
    case 'executed': return 'Executed'
    case 'awaiting_verification': return 'Awaiting Measurement'
    case 'verified': case 'rewarded': return 'Verified'
    case 'closed': return 'Resolved'
    case 'dismissed': return 'Dismissed'
    case 'failed': return 'Failed'
    default: return humanize(s) || 'Unknown'
  }
}

const STATUS_LABEL: Record<string, string> = {
  detected: 'Detected', investigating: 'Investigating', needs_review: 'Needs Review', ready_for_action: 'Ready for Action',
  executing: 'Executing', awaiting_measurement: 'Awaiting Measurement', verified: 'Verified', resolved: 'Resolved',
  dismissed: 'Dismissed', failed: 'Failed'
}

/** Prefer the backend's UI status (UI.md §26); fall back to mapping the raw state. */
export function incidentStatusLabel(i: { status?: string | null; state?: string | null }): string {
  return (i.status && STATUS_LABEL[i.status]) || stateLabel(i.state)
}

export function stateTone(s?: string | null): 'info' | 'warn' | 'good' | 'bad' | 'muted' | 'policy' {
  switch (STATUS_LABEL[s ?? ''] ?? stateLabel(s)) {
    case 'Investigating': case 'Executing': return 'info'
    case 'Needs Review': case 'Ready for Action': case 'Awaiting Measurement': case 'Executed': return 'warn'
    case 'Verified': case 'Resolved': return 'good'
    case 'Failed': return 'bad'
    default: return 'muted'
  }
}

export function experimentLabel(s?: ExperimentStatus | string | null): string {
  switch (s) {
    case 'proposed': return 'Proposed'
    case 'approved': return 'Approved'
    case 'executing': return 'Running'
    case 'executed': return 'Running'
    case 'awaiting_verification': return 'Awaiting Measurement'
    case 'verified': case 'rewarded': return 'Verified'
    case 'rejected': return 'Rejected'
    case 'failed': return 'Failed'
    default: return humanize(s) || 'Unknown'
  }
}

export function experimentTone(s?: string | null): 'info' | 'warn' | 'good' | 'bad' | 'muted' {
  switch (s) {
    case 'executing': case 'executed': case 'approved': return 'info'
    case 'awaiting_verification': case 'proposed': return 'warn'
    case 'verified': case 'rewarded': return 'good'
    case 'failed': case 'rejected': return 'bad'
    default: return 'muted'
  }
}

export function stepTone(s: StepStatus): 'info' | 'warn' | 'good' | 'bad' | 'muted' {
  return ({ pending: 'muted', running: 'info', success: 'good', warning: 'warn', failed: 'bad', waiting: 'warn' } as const)[s] ?? 'muted'
}

export const ACTION_LABEL: Record<string, string> = {
  observe: 'Observe',
  update_existing_page: 'Update existing page',
  create_faq: 'Add FAQ section',
  create_canonical_page: 'Create canonical page',
  create_comparison_content: 'Create comparison content',
  publisher_outreach: 'Publisher outreach',
  structured_data: 'Structured data update'
}

export function actionLabel(a?: string | null): string {
  return (a && ACTION_LABEL[a]) || humanize(a) || '—'
}

export function basisLabel(basis?: string | null, policyVersion?: string | null, n?: number | null): string {
  switch (basis) {
    case 'cold_start_prior': return 'Cold-start prior · no related verified experiments yet'
    case 'learned_policy': return `Policy ${policyVersion ?? ''} · learned${n != null ? ` from ${n} related experiments` : ''}`.replace('  ', ' ')
    case 'rule_fallback': return 'Rule fallback'
    case 'manual_override': return 'Manual override'
    default: return basis ? humanize(basis) : 'Selection basis not reported'
  }
}

export function metricTrendText(label: string, trend?: number[] | null): string {
  if (!trend || trend.length < 2) return ''
  const first = trend[0]!
  const last = trend[trend.length - 1]!
  return `${label} trend: ${last > first ? 'rising' : last < first ? 'falling' : 'flat'} over ${trend.length} points`
}

/** Executor names are wire values; labels are operator-facing. Manual is the default and always available. */
export function executorLabel(name?: string | null): string {
  switch (name) {
    case undefined: case null: case '': case 'manual': return 'Manual'
    case 'github_pr': case 'github': return 'GitHub PR (optional)'
    case 'profound_agent': return 'Profound agent'
    default: return humanize(name)
  }
}

/** Paths touched by a proposed change (the backend sends file objects; the strings are display-only). */
export function changeFilePaths(change?: { files?: unknown } | null): string[] {
  const files = Array.isArray(change?.files) ? (change!.files as unknown[]) : []
  return files.map((f) => (typeof f === 'string' ? f : (f as { path?: string })?.path ?? '')).filter(Boolean)
}

/** "in 3h" / "2h ago" for a future/past timestamp (display only; never used for gating). */
export function relativeUntil(iso?: string | null, now: number = Date.now()): string {
  if (!iso) return '—'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  if (t <= now) return relativeTime(iso, now)
  const m = Math.round((t - now) / 60000)
  if (m < 60) return `in ${m}m`
  const h = Math.round(m / 60)
  return h < 48 ? `in ${h}h` : `in ${Math.round(h / 24)}d`
}
