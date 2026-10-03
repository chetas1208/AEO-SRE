import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ConfidenceBadge from '~/components/shared/ConfidenceBadge.vue'
import ErrorState from '~/components/shared/ErrorState.vue'
import ExpectedOutcomePanel from '~/components/incidents/ExpectedOutcomePanel.vue'
import SeverityBadge from '~/components/shared/SeverityBadge.vue'
import InterventionPackage from '~/components/incidents/InterventionPackage.vue'
import StatusBadge from '~/components/shared/StatusBadge.vue'
import { vi } from 'vitest'

describe('shared components', () => {
  it('ConfidenceBadge shows number and label', () => {
    expect(mount(ConfidenceBadge, { props: { value: 0.84 } }).text()).toBe('0.84 · High confidence')
    expect(mount(ConfidenceBadge, { props: { value: null } }).text()).toContain('not reported')
  })
  it('ErrorState distinguishes unavailable from failed', () => {
    const u = mount(ErrorState, { props: { surface: 'Evidence', error: { kind: 'unavailable', message: 'API is unreachable.' } } })
    expect(u.text()).toContain('Unavailable data')
    const f = mount(ErrorState, { props: { surface: 'Evidence', error: { kind: 'failed', status: 500, message: 'API request failed (HTTP 500).' } } })
    expect(f.text()).toContain('Failed data')
    expect(f.text()).toContain('Evidence request failed.')
  })
  it('SeverityBadge renders backend severity label', () => {
    expect(mount(SeverityBadge, { props: { severity: 'critical' } }).text()).toBe('Critical')
  })
  it('ExpectedOutcome: range only with n, else insufficient history', () => {
    const ok = mount(ExpectedOutcomePanel, { props: { outcome: { available: true, n: 12, low: 0.08, high: 0.15, unit: 'fraction', metric: 'visibility' } } })
    expect(ok.text()).toContain('+8 to +15pp')
    expect(ok.text()).toContain('n = 12')
    const none = mount(ExpectedOutcomePanel, { props: { outcome: { available: false, n: 0 } } })
    expect(none.text()).toContain('Insufficient experiment history to estimate outcome.')
    expect(mount(ExpectedOutcomePanel, { props: { outcome: null } }).text()).toContain('Insufficient experiment history')
  })
})

describe('InterventionPackage (manual executor)', () => {
  const recordExecuted = vi.fn().mockResolvedValue({})
  Object.assign(globalThis, { useApproveIntervention: () => ({ submitting: { value: false }, recordExecuted }) })
  const candidate: any = {
    id: 'iv-1', action: 'create_faq', title: 'FAQ', executor: 'manual', manualExecutionPending: true, executionTarget: 'https://a.example/faq',
    observationWindow: 'after a 48h data lag, measured over 7 days',
    package: { steps: ['Create the page', "Come back here and click 'Mark as executed'"], rollback: 'remove page', risk: 'low', evidenceSummary: [{ title: 'Source', type: 'owned', status: 'live' }],
      changes: [{ path: 'faq/sso.md', proposedText: '# FAQ', diff: '+# FAQ', currentContentKnown: false }], target: { url: 'https://a.example/faq', paths: ['faq/sso.md'] }, requiresHumanStep: true }
  }
  const mountIt = (c = candidate) => mount(InterventionPackage, { props: { candidate: c, incidentId: 'inc-1' }, global: { components: { StatusBadge } } })

  it('shows the exact change, steps, target and executor, with a Mark as executed form', () => {
    const w = mountIt()
    expect(w.text()).toContain('Executor: Manual')
    expect(w.text()).toContain('https://a.example/faq')
    expect(w.get('[data-testid="package-diff"]').text()).toBe('+# FAQ')
    expect(w.get('[data-testid="package-steps"]').text()).toContain('Create the page')
    expect(w.find('[data-testid="mark-executed-form"]').exists()).toBe(true)
    expect(w.text()).not.toMatch(/GitHub|pull request|branch/i)
  })

  it('"I changed something different" requires the pasted change and posts it verbatim', async () => {
    const w = mountIt()
    await w.get('[data-testid="different-toggle"]').setValue(true)
    expect(w.get('[data-testid="mark-executed-submit"]').attributes('disabled')).toBeDefined()
    await w.get('[data-testid="actual-change"]').setValue('  my own wording ')
    expect(w.get('[data-testid="mark-executed-submit"]').attributes('disabled')).toBeUndefined()
    await w.get('[data-testid="mark-executed-form"]').trigger('submit')
    expect(recordExecuted).toHaveBeenCalledWith('iv-1', expect.objectContaining({ actualChange: '  my own wording ' }))
  })

  it('after the execution is recorded the form is replaced by the recorded fact (no optimistic state)', () => {
    const w = mountIt({ ...candidate, manualExecutionPending: false, executionStatus: 'succeeded', executedBy: 'alice', deviation: true, actualChange: 'x' })
    expect(w.find('[data-testid="mark-executed-form"]').exists()).toBe(false)
    expect(w.text()).toContain('Executed by alice')
    expect(w.text()).toContain('Deviation from proposal')
  })
})
