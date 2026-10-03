// Frontend half of the UI/API contract: generated types carry the fields the UI renders, error envelope is read
// code-first, and the global SSE feed drives the Live pill. Payloads are TEST FIXTURES.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { computed, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { camelize } from '~/utils/camelize'
import { normExperimentDetail, normExperimentRow } from '~/utils/normalize'
import { isAlreadyApplied, toApiError } from '~/composables/useApi'

const generated = readFileSync(join(__dirname, '..', 'types', 'api.generated.ts'), 'utf8')
const block = (name: string) => {
  const i = generated.indexOf(`        ${name}: {`)
  expect(i, `schema ${name} missing from generated types (run pnpm gen:api)`).toBeGreaterThan(-1)
  return generated.slice(i, generated.indexOf('\n        };', i))
}

describe('generated API types carry the experiment fields the UI renders', () => {
  it('ExperimentDetail', () => {
    const b = block('ExperimentDetail')
    for (const f of ['display_status', 'spec', 'declared_metrics', 'outcome', 'override', 'verification', 'awaiting_reward']) expect(b).toContain(`${f}`)
  })
  it('ExperimentSpec / DeclaredMetrics / OutcomeOut / OverrideOut / VerificationInfo', () => {
    for (const f of ['if_action', 'because_root_cause', 'then_metric', 'window_hours']) expect(block('ExperimentSpec')).toContain(f)
    for (const f of ['primary', 'secondary']) expect(block('DeclaredMetrics')).toContain(f)
    for (const f of ['label', 'causal_confidence', 'confounders', 'inconclusive_reason', 'reward_total', 'learning_applied']) expect(block('OutcomeOut')).toContain(f)
    for (const f of ['policy_action', 'executed_action', 'reason', 'overridden']) expect(block('OverrideOut')).toContain(f)
    for (const f of ['eligible_at', 'window_end', 'rules', 'is_open']) expect(block('VerificationInfo')).toContain(f)
  })
  it('ExperimentRow has outcome + display_status', () => {
    const b = block('ExperimentRow')
    expect(b).toContain('outcome')
    expect(b).toContain('display_status')
  })
  it('error envelope and both SSE routes are typed', () => {
    expect(block('ErrorEnvelope')).toContain('error')
    expect(block('ErrorBody')).toContain('code')
    expect(generated).toContain('text/event-stream')
    expect(generated).toContain('"/api/events"')
  })
})

describe('normalizers keep unknown as null (never a default)', () => {
  it('detail without B4 data', () => {
    const d = normExperimentDetail(camelize({ id: 'e1', code: 'EXP-0001', summary: { status: 'awaiting_verification' }, spec: null, outcome: null, override: null, verification: null }))
    expect(d.spec).toBeNull()
    expect(d.outcome).toBeNull()
    expect(d.declaredMetrics).toBeNull()
    expect(d.outcomeLabel).toBeNull()
    expect(d.policyVersion).toBeNull()
  })
  it('inconclusive outcome carries label + reason, list row carries outcome', () => {
    const d = normExperimentDetail(camelize({
      id: 'e1', summary: { status: 'verified' }, display_status: 'Inconclusive',
      outcome: { label: 'inconclusive', inconclusive_reason: 'primary metric unavailable before or after', confounders: [{ kind: 'new_prompt', hard: false }], learning_applied: false, components: {} }
    }))
    expect(d.displayStatus).toBe('Inconclusive')
    expect(d.outcomeLabel).toBe('inconclusive')
    expect(d.inconclusiveReason).toContain('primary metric')
    expect(d.outcome?.confounders[0]?.kind).toBe('new_prompt')
    const row = normExperimentRow(camelize({ id: 'e1', status: 'verified', display_status: 'Inconclusive', outcome: 'inconclusive', inconclusive_reason: 'x' }))
    expect(row.outcomeLabel).toBe('inconclusive')
  })
})

describe('error envelope', () => {
  beforeEach(() => { Object.assign(globalThis, { absoluteTime: (s: string) => `T(${s})` }) })
  it('reads error.code first, falls back to error.type', () => {
    expect(toApiError({ statusCode: 409, data: { error: { code: 'ACTION_NOT_ELIGIBLE', type: 'conflict', message: 'm', details: { a: 1 }, request_id: 'r1' } } }))
      .toMatchObject({ code: 'ACTION_NOT_ELIGIBLE', requestId: 'r1', details: { a: 1 }, status: 409 })
    expect(toApiError({ statusCode: 409, data: { error: { type: 'conflict', message: 'm' } } }).code).toBe('conflict')
  })
  it('EXPERIMENT_NOT_VERIFIABLE_YET shows an eligible-at message', () => {
    const e = toApiError({ statusCode: 409, data: { error: { code: 'EXPERIMENT_NOT_VERIFIABLE_YET', type: 'conflict', message: 'too early', details: { eligible_at: '2026-10-04T20:29:43+00:00' } } } })
    expect(e.message).toContain('eligible at 2026-10-04T20:29:43+00:00')
    expect(isAlreadyApplied(e)).toBe(false)
  })
  it('503 / network failures are unavailable, not failed', () => {
    expect(toApiError({ statusCode: 503, data: { error: { code: 'DATABASE_UNAVAILABLE', type: 'unavailable', message: 'x' } } }).kind).toBe('unavailable')
    expect(toApiError(new Error('fetch failed')).kind).toBe('unavailable')
  })
  it('409 already_* is applied; other 409s are not', () => {
    expect(isAlreadyApplied({ status: 409, details: { reason: 'already_executed' } })).toBe(true)
    expect(isAlreadyApplied({ status: 409, code: 'INVALID_STATE_TRANSITION' })).toBe(false)
    expect(isAlreadyApplied({ status: 500 })).toBe(false)
  })
})

class FakeES {
  static all: FakeES[] = []
  listeners: Record<string, ((m: { data: string }) => void)[]> = {}
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeES.all.push(this) }
  addEventListener(n: string, f: (m: { data: string }) => void) { (this.listeners[n] ??= []).push(f) }
  close() { this.closed = true }
  emit(n: string, d: unknown) { for (const f of this.listeners[n] ?? []) f({ data: JSON.stringify(d) }) }
}

describe('global SSE feed -> Live pill state', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeES.all = []
    Object.assign(globalThis, { EventSource: FakeES, ref, computed, camelize, useApiBase: () => 'http://api.test', apiFetch: vi.fn(), normHealth: () => ({ capabilities: [] }) })
    setActivePinia(createPinia())
  })
  afterEach(() => vi.useRealTimers())

  async function store() {
    const { useLiveSystemStore } = await import('~/stores/liveSystem')
    return useLiveSystemStore()
  }

  it('connects to /api/events, tracks heartbeat age, dedupes incident events, closes on stop', async () => {
    const s = await store()
    s.apiReachable = true
    s.startGlobalStream()
    expect(FakeES.all[0]!.url).toBe('http://api.test/api/events')
    FakeES.all[0]!.onopen?.()
    expect(s.globalStream).toBe('connected')
    expect(s.liveState).toBe('live')
    vi.advanceTimersByTime(4000)
    FakeES.all[0]!.emit('heartbeat', { type: 'heartbeat' })
    expect(s.heartbeatAgeSeconds).toBe(0)
    vi.advanceTimersByTime(3000)
    expect(s.heartbeatAgeSeconds).toBe(3)
    const t0 = s.incidentEventTick
    FakeES.all[0]!.emit('incident_event', { type: 'incident_event', id: 'a', stage: 'detected' })
    FakeES.all[0]!.emit('incident_event', { type: 'incident_event', id: 'a', stage: 'detected' })
    expect(s.incidentEventTick).toBe(t0 + 1)
    s.stopGlobalStream()
    expect(FakeES.all[0]!.closed).toBe(true)
    expect(s.globalStream).toBe('idle')
  })

  it('error -> reconnecting with backoff, then connected again and lists are told to refetch', async () => {
    const s = await store()
    s.apiReachable = true
    s.startGlobalStream()
    FakeES.all[0]!.onopen?.()
    FakeES.all[0]!.onerror?.()
    expect(s.globalStream).toBe('reconnecting')
    expect(s.liveState).toBe('reconnecting')
    expect(FakeES.all.length).toBe(1)
    vi.advanceTimersByTime(1000)
    expect(FakeES.all.length).toBe(2)
    const t0 = s.incidentEventTick
    FakeES.all[1]!.onopen?.()
    expect(s.liveState).toBe('live')
    expect(s.incidentEventTick).toBe(t0 + 1)
    s.stopGlobalStream()
  })

  it('a silent connection (no heartbeat for 25s) is torn down and retried', async () => {
    const s = await store()
    s.apiReachable = true
    s.startGlobalStream()
    FakeES.all[0]!.onopen?.()
    vi.advanceTimersByTime(27_000)
    expect(FakeES.all[0]!.closed).toBe(true)
    expect(s.globalStream).toBe('reconnecting')
    s.stopGlobalStream()
  })
})
