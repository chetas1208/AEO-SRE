import { describe, expect, it } from 'vitest'
import { camelize } from '~/utils/camelize'
import { formatDeltaValue, formatValue, confidenceLabel, incidentStatusLabel, basisLabel, executorLabel, changeFilePaths } from '~/utils/format'
import { normGraph, normIncidentList, normInterventions, normIntervention, normExperimentDetail } from '~/utils/normalize'
import { layoutGraph } from '~/utils/graphLayout'
import { primaryCta, attentionCount, resolveCta } from '~/utils/incidentState'

// Payloads below are TEST FIXTURES shaped like the A12 API (snake_case). They are not application data.
describe('camelize', () => {
  it('camelizes keys but preserves opaque dictionaries', () => {
    const out: any = camelize({ detected_at: 'x', counts_by_status: { needs_review: 2 }, proposed_change: { diff_text: 'a' }, items: [{ first_name: 1 }] })
    expect(out.detectedAt).toBe('x')
    expect(out.countsByStatus.needs_review).toBe(2)
    expect(out.proposedChange.diff_text).toBe('a')
    expect(out.items[0].firstName).toBe(1)
  })
})

describe('format', () => {
  it('formats pp metrics and deltas', () => {
    expect(formatValue(37, 'pp')).toBe('37%')
    expect(formatDeltaValue(-24, 'pp')).toBe('−24pp')
    expect(formatDeltaValue(3, 'count')).toBe('+3')
    expect(formatValue(null, 'pp')).toBe('—')
  })
  it('confidence label is number-independent text', () => {
    expect(confidenceLabel(0.84)).toBe('High confidence')
    expect(confidenceLabel(0.63)).toBe('Medium confidence')
    expect(confidenceLabel(0.31)).toBe('Low confidence')
  })
  it('status and basis copy', () => {
    expect(incidentStatusLabel({ status: 'needs_review', state: 'evidence_ready' })).toBe('Needs Review')
    expect(basisLabel('cold_start_prior')).toContain('Cold-start prior')
    expect(basisLabel('learned_policy', 'v0.4.2', 38)).toBe('Policy v0.4.2 · learned from 38 related experiments')
  })
})

describe('normalizers', () => {
  it('incident list with counts', () => {
    const raw = camelize({ items: [{ id: 'a', title: 't', severity: 'high', state: 'detected', status: 'detected', detected_at: 'x' }], total: 1, counts_by_severity: { all: 1, high: 1 }, counts_by_status: { all: 1, detected: 1 }, counts_by_state: {} })
    const list = normIncidentList(raw)
    expect(list.items[0]!.detectedAt).toBe('x')
    expect(list.counts.bySeverity.high).toBe(1)
    expect(attentionCount(list)).toBe(1)
  })
  it('interventions: selected first, cold start from basis', () => {
    const set = normInterventions(camelize({ items: [{ id: 'b', action: 'observe', title: 'Observe', score: 0.1, selected: false }, { id: 'a', action: 'update_existing_page', title: 'U', score: 0.7, selected: true, selection_basis: 'cold_start_prior', observation_window_hours: 72 }] }))
    expect(set.items[0]!.id).toBe('a')
    expect(set.items[0]!.coldStart).toBe(true)
    expect(set.items[0]!.observationWindow).toBe('72h')
  })
  it('experiment detail sections', () => {
    const d = normExperimentDetail(camelize({ id: 'e', code: 'EXP-0001', summary: { status: 'executed', display_status: 'Awaiting Measurement', incident: { id: 'i', title: 'T' } }, why_selected: { reason: 'r', policy_version: 'v0.0.1' }, reward: null, awaiting_reward: true, timeline: [] }))
    expect(d.code).toBe('EXP-0001')
    expect(d.incidentTitle).toBe('T')
    expect(d.awaitingReward).toBe(true)
    expect(d.rewardData).toBeNull()
  })
  it('graph maps backend GraphOut and lays out by level', () => {
    const g = normGraph(camelize({ nodes: [{ id: 'r', node_class: 'profound', title: 'Root', level: 0 }, { id: 'c', node_class: 'competitor', title: 'C', level: 1 }], edges: [{ id: 'e', source: 'r', target: 'c', type: 'triggered' }], validation: { is_acyclic: true, warnings: [] } }))
    expect(g.edges[0]).toMatchObject({ src: 'r', dst: 'c', edgeType: 'triggered' })
    const l = layoutGraph(g)
    expect(l.nodes.find((n) => n.id === 'c')!.y).toBeGreaterThan(l.nodes.find((n) => n.id === 'r')!.y)
  })
})

describe('primaryCta', () => {
  it('single CTA follows lifecycle state', () => {
    expect(primaryCta('detected', false).label).toBe('Investigate')
    expect(primaryCta('investigating', false).disabled).toBe(true)
    expect(primaryCta('awaiting_approval', true).label).toBe('Approve')
    expect(primaryCta('approved', true)).toMatchObject({ kind: 'mark_executed', label: 'Mark as executed' })
    expect(primaryCta('awaiting_verification', true).label).toBe('Awaiting Measurement')
  })
})

describe('manual executor wiring', () => {
  it('maps the backend CTA keys approve / mark_executed', () => {
    const base: any = { state: 'approved', primaryCta: { key: 'mark_executed', label: 'Mark as executed', enabled: true, interventionId: 'iv' } }
    expect(resolveCta(base, true)).toMatchObject({ kind: 'mark_executed', disabled: false, interventionId: 'iv' })
    expect(resolveCta({ state: 'awaiting_approval', primaryCta: { key: 'approve', label: 'Approve', enabled: true } } as any, true).kind).toBe('approve')
  })
  it('normalizes the intervention package and executor, defaulting to Manual', () => {
    const iv = normIntervention(camelize({
      id: 'iv', action: 'create_faq', title: 'FAQ', approval_status: 'approved', manual_execution_pending: true,
      proposed_change: { files: [{ path: 'faq/sso.md', new_content: 'x' }] },
      package: { steps: ['Do it'], rollback: 'remove page', evidence_summary: [{ title: 'E' }], changes: [{ path: 'faq/sso.md', proposed_text: 'x', diff: '+x', current_content_known: false }],
        observation_window: { delay_hours: 48, duration_days: 7 }, target: { url: 'https://a.example/faq', paths: ['faq/sso.md'] } },
      execution: { executor: 'manual', status: 'awaiting_human_execution', dry_run: false, deviation: false }
    }))
    expect(iv.executor).toBe('manual')
    expect(iv.manualExecutionPending).toBe(true)
    expect(iv.dryRun).toBe(false)
    expect(iv.package?.changes[0]).toMatchObject({ path: 'faq/sso.md', proposedText: 'x' })
    expect(iv.package?.target?.url).toBe('https://a.example/faq')
    expect(iv.rollback).toBe('remove page')
    expect(iv.observationWindow).toContain('48h')
    expect(changeFilePaths(iv.proposedChange)).toEqual(['faq/sso.md'])
    expect(executorLabel(iv.executor)).toBe('Manual')
    expect(executorLabel('github_pr')).toContain('optional')
    expect(normIntervention(camelize({ id: 'x' })).executor).toBe('manual')
  })
})
