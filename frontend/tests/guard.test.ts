import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { camelize } from '~/utils/camelize'
import { normChangeCheck, normChangeCheckList, normProtection, normCanonicalClaims, normIntervention, normExperimentDetail } from '~/utils/normalize'
import { decisionMeta, approveGate, semanticCheckLabel, guardErrorMessage } from '~/utils/guard'
import { toApiError } from '~/composables/useApi'
import DecisionChip from '~/components/guard/DecisionChip.vue'
import GuardVerdict from '~/components/guard/GuardVerdict.vue'
import CheckCard from '~/components/guard/CheckCard.vue'
import ProtectionBanner from '~/components/guard/ProtectionBanner.vue'

// Payloads are TEST FIXTURES shaped by docs/CHANGE_GUARD_SPEC.md (snake_case). They are not application data.
const rawCheck = (over: Record<string, unknown> = {}) => camelize({
  id: 'chk-1', decision: 'DELAY', agent: { id: 'a1', name: 'Test Agent' }, source_mode: 'SIMULATED', target_url: 'https://example.test/pricing',
  action_type: 'update_existing_page', eligible_after: '2026-10-04T20:29:43Z', semantic_check: 'skipped_no_canonical_truth',
  findings: [{ type: 'active_experiment_contamination', severity: 'high', decision: 'DELAY', reason: 'Target overlaps EXP-0001', eligible_after: '2026-10-04T20:29:43Z',
    target_overlap_pct: 100, prompt_cluster_overlap_pct: 40, references: { experiment_code: 'EXP-0001' } }],
  evaluated_at: '2026-10-03T10:00:00Z', ...over
})

describe('guard normalizers', () => {
  it('maps a check, its findings and references', () => {
    const c = normChangeCheck(rawCheck())
    expect(c.decision).toBe('DELAY')
    expect(c.agentName).toBe('Test Agent')
    expect(c.sourceMode).toBe('SIMULATED')
    expect(c.eligibleAfter).toBe('2026-10-04T20:29:43Z')
    expect(c.findings?.[0]).toMatchObject({ targetOverlapPct: 100, promptClusterOverlapPct: 40, experimentCode: 'EXP-0001', reason: 'Target overlaps EXP-0001' })
    expect(c.experimentCodes).toEqual(['EXP-0001'])
  })
  it('unknown or missing values stay null, never defaulted', () => {
    const c = normChangeCheck({ id: 'x', decision: 'MAYBE' })
    expect(c.decision).toBeNull()
    expect(c.sourceMode).toBeNull()
    expect(c.findings).toBeNull()
    expect(c.semanticCheck).toBeNull()
    expect(c.checksRun).toBeNull()
    expect(c.eligibleAfter).toBeNull()
  })
  it('list wraps items and paging fields', () => {
    const l = normChangeCheckList(camelize({ items: [{ id: 'a', decision: 'allow' }], total: 11, limit: 10, offset: 0 }))
    expect(l.items[0]!.decision).toBe('ALLOW')
    expect(l.total).toBe(11)
  })
  it('protection absent is null; present keeps protected flag', () => {
    expect(normProtection(undefined)).toBeNull()
    const p = normProtection(camelize({ protected: true, until: '2026-10-04T20:29:43Z', targets: ['/pricing'], checks_blocked_count: 2, recent_checks: [{ id: 'a', decision: 'DELAY' }] }))
    expect(p).toMatchObject({ protected: true, until: '2026-10-04T20:29:43Z', targets: ['/pricing'], checksBlockedCount: 2 })
    expect(p?.recentChecks).toHaveLength(1)
    expect(normProtection(camelize({ checks_blocked_count: 1 }))?.protected).toBeNull()
  })
  it('experiment detail carries protection; intervention carries guard', () => {
    const e = normExperimentDetail(camelize({ id: 'e1', summary: {}, protection: { protected: true, until: '2026-10-04T20:29:43Z', targets: [], recent_checks: [] } }))
    expect(e.protection?.protected).toBe(true)
    expect(normExperimentDetail(camelize({ id: 'e2', summary: {} })).protection).toBeNull()
    const i = normIntervention(camelize({ id: 'i1', action: 'create_faq', change_guard: { decision: 'BLOCK', findings: [] } }))
    expect(i.guard?.decision).toBe('BLOCK')
    expect(normIntervention(camelize({ id: 'i2', action: 'create_faq' })).guard).toBeNull()
  })
  it('canonical claims', () => {
    const l = normCanonicalClaims(camelize({ items: [{ id: 'c1', statement: 'We do not offer X', entities: ['X'], valid_from: '2026-01-01', status: 'active' }] }))
    expect(l[0]).toMatchObject({ statement: 'We do not offer X', entities: ['X'], validFrom: '2026-01-01', source: null })
  })
})

describe('decision chip mapping', () => {
  it('every decision has a text label, glyph and stable tone', () => {
    expect(decisionMeta('ALLOW')).toMatchObject({ label: 'ALLOW', tone: 'good' })
    expect(decisionMeta('MERGE').tone).toBe('info')
    expect(decisionMeta('DELAY').tone).toBe('warn')
    expect(decisionMeta('REQUIRE_REVIEW').label).toBe('REQUIRE REVIEW')
    expect(decisionMeta('BLOCK').tone).toBe('bad')
    for (const d of ['ALLOW', 'MERGE', 'DELAY', 'REQUIRE_REVIEW', 'BLOCK'] as const) expect(decisionMeta(d).glyph).not.toBe('')
  })
  it('null is Unavailable (muted), never a decision', () => {
    expect(decisionMeta(null)).toMatchObject({ label: 'Unavailable', tone: 'muted' })
    expect(mount(DecisionChip, { props: { decision: null } }).text()).toContain('Unavailable')
    expect(mount(DecisionChip, { props: { decision: 'BLOCK' } }).text()).toContain('BLOCK')
  })
})

describe('approve gating', () => {
  const g = (decision: any, extra: Record<string, unknown> = {}) => normChangeCheck(rawCheck({ decision, ...extra }))
  it('BLOCK and DELAY disable approve and explain', () => {
    const b = approveGate(g('BLOCK'))
    expect(b).toMatchObject({ allowed: false, needsReason: false })
    expect(b.message).toContain('Target overlaps')
    const d = approveGate(g('DELAY'))
    expect(d.allowed).toBe(false)
    expect(d.eligibleAfter).toBe('2026-10-04T20:29:43Z')
  })
  it('REQUIRE_REVIEW needs a non-blank reason', () => {
    expect(approveGate(g('REQUIRE_REVIEW'), '')).toMatchObject({ allowed: false, needsReason: true })
    expect(approveGate(g('REQUIRE_REVIEW'), '   ').allowed).toBe(false)
    expect(approveGate(g('REQUIRE_REVIEW'), 'Checked with owner')).toMatchObject({ allowed: true, needsReason: true })
  })
  it('ALLOW and MERGE do not block', () => {
    expect(approveGate(g('ALLOW')).allowed).toBe(true)
    expect(approveGate(g('MERGE')).allowed).toBe(true)
  })
  it('missing verdict is unavailable, not a pass', () => {
    const u = approveGate(null)
    expect(u.unavailable).toBe(true)
    expect(u.message).toContain('unavailable')
    expect(approveGate(normChangeCheck({ id: 'x' })).unavailable).toBe(true)
  })
})

describe('guard components', () => {
  it('GuardVerdict BLOCK shows explanation; DELAY shows eligible_after', () => {
    const w = mount(GuardVerdict, { props: { guard: normChangeCheck(rawCheck()) } })
    expect(w.text()).toContain('Approval is disabled')
    expect(w.find('[data-testid="eligible-after"]').exists()).toBe(true)
  })
  it('REQUIRE_REVIEW renders a reason box and emits input', async () => {
    const w = mount(GuardVerdict, { props: { guard: normChangeCheck(rawCheck({ decision: 'REQUIRE_REVIEW' })), reviewReason: '' } })
    await w.find('[data-testid="review-reason"]').setValue('ok')
    expect(w.emitted('update:reviewReason')?.[0]).toEqual(['ok'])
  })
  it('MERGE shows the suggested merged proposal', () => {
    const w = mount(GuardVerdict, { props: { guard: normChangeCheck(rawCheck({ decision: 'MERGE', merged_proposal: 'merged text' })) } })
    expect(w.find('[data-testid="merged-proposal"]').text()).toBe('merged text')
  })
  it('ALLOW is quiet and never implies a pass for a skipped/degraded check', () => {
    const w = mount(GuardVerdict, { props: { guard: normChangeCheck(rawCheck({ decision: 'ALLOW', findings: [], semantic_check: 'degraded', checks_run: ['active_experiment', 'duplicate_change'], checks_skipped: ['canonical_truth'] })) } })
    expect(w.text()).toContain('No conflicts found')
    expect(w.text()).toContain('Skipped: Canonical truth')
    expect(w.find('[data-testid="semantic-state"]').text()).toContain('degraded')
    expect(w.text()).not.toContain('Semantic check ran')
    expect(semanticCheckLabel('skipped_no_canonical_truth')).toContain('skipped')
  })
  it('no verdict renders unavailable', () => {
    expect(mount(GuardVerdict, { props: { guard: null } }).find('[data-testid="guard-unavailable"]').exists()).toBe(true)
  })
  it('CheckCard shows SIMULATED AGENT, agent, target, overlap and reasons', () => {
    const w = mount(CheckCard, { props: { check: normChangeCheck(rawCheck()) }, global: { stubs: { NuxtLink: { template: '<a><slot /></a>' } } } })
    expect(w.text()).toContain('SIMULATED AGENT')
    expect(w.text()).toContain('Test Agent')
    expect(w.text()).toContain('https://example.test/pricing')
    expect(w.text()).toContain('Target overlap 100%')
    expect(w.text()).toContain('Prompt-cluster overlap 40%')
    const live = mount(CheckCard, { props: { check: normChangeCheck(rawCheck({ source_mode: 'LIVE' })) }, global: { stubs: { NuxtLink: true } } })
    expect(live.text()).not.toContain('SIMULATED AGENT')
  })
  it('ProtectionBanner only when protected is true; shows absolute UTC', () => {
    const on = mount(ProtectionBanner, { props: { protection: normProtection(camelize({ protected: true, until: '2026-10-04T20:29:43Z', targets: [] })) } })
    expect(on.text()).toContain('Protected until 2026-10-04 20:29 UTC')
    expect(mount(ProtectionBanner, { props: { protection: normProtection(camelize({ protected: false })) } }).find('[data-testid="protection-banner"]').exists()).toBe(false)
    expect(mount(ProtectionBanner, { props: { protection: null } }).find('[data-testid="protection-banner"]').exists()).toBe(false)
  })
})

describe('guard API errors', () => {
  it('maps CHANGE_GUARD_BLOCKED and APPROVAL_DIGEST_MISMATCH by error.code', () => {
    const e = toApiError({ statusCode: 409, data: { error: { code: 'APPROVAL_DIGEST_MISMATCH', type: 'conflict', message: 'm' } } })
    expect(e.code).toBe('APPROVAL_DIGEST_MISMATCH')
    expect(e.message).toContain('proposal changed since approval; re-approve')
    expect(toApiError({ statusCode: 409, data: { error: { code: 'CHANGE_GUARD_BLOCKED', type: 'conflict', message: 'm' } } }).message).toContain('change guard')
    expect(guardErrorMessage('OTHER')).toBeUndefined()
  })
})
