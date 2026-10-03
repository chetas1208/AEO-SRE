// Composable behaviour tests with stubbed Nuxt auto-imports. Payloads are TEST FIXTURES (RECORDED/TEST ONLY).
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { camelize } from '~/utils/camelize'
import { normEvent } from '~/utils/normalize'
import { isAlreadyApplied } from '~/composables/useApi'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  listeners: Record<string, ((m: { data: string }) => void)[]> = {}
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeEventSource.instances.push(this) }
  addEventListener(name: string, fn: (m: { data: string }) => void) { (this.listeners[name] ??= []).push(fn) }
  close() { this.closed = true }
  emit(name: string, data: unknown) { for (const fn of this.listeners[name] ?? []) fn({ data: typeof data === 'string' ? data : JSON.stringify(data) }) }
}

const setStream = vi.fn()
const ev = (seq: number, stage: string, status = 'success', ts = `2026-10-02T11:24:0${seq}Z`) =>
  ({ id: `evt_${seq}`, seq, incident_id: '1042', timestamp: ts, stage, status, message: `msg ${stage}`, metadata: { i: seq } })

async function mountEvents(onTerminal?: () => void) {
  Object.assign(globalThis, {
    EventSource: FakeEventSource, useLiveSystemStore: () => ({ setStream }), useApiBase: () => 'http://api.test',
    MaybeRefOrGetter: undefined, toValue: (v: any) => (typeof v === 'function' ? v() : v?.value ?? v), watch: (await import('vue')).watch,
    onMounted: (await import('vue')).onMounted, onBeforeUnmount: (await import('vue')).onBeforeUnmount, ref, camelize, normEvent
  })
  const { useIncidentEvents } = await import('~/composables/useIncidentEvents')
  let api: ReturnType<typeof useIncidentEvents>
  const wrapper = mount(defineComponent({ setup() { api = useIncidentEvents('inc-1', onTerminal); return () => h('div') } }))
  return { api: api!, es: () => FakeEventSource.instances.at(-1)!, wrapper }
}

describe('useIncidentEvents (SSE)', () => {
  beforeEach(() => { FakeEventSource.instances = []; setStream.mockClear() })

  it('connects to the incident events endpoint and reports connection state', async () => {
    const { api, es } = await mountEvents()
    expect(es().url).toBe('http://api.test/api/incidents/inc-1/events')
    es().onopen?.()
    expect(api.state.value).toBe('connected')
    es().onerror?.()
    expect(api.state.value).toBe('disconnected')
    expect(setStream).toHaveBeenCalledWith('disconnected')
  })

  it('renders events chronologically and de-duplicates replayed ids', async () => {
    const { api, es } = await mountEvents()
    es().emit('message', ev(2, 'crawl'))
    es().emit('message', ev(1, 'fetch_prompts'))
    es().emit('message', ev(2, 'crawl')) // replay after reconnect
    expect(api.events.value.map((e) => e.stage)).toEqual(['fetch_prompts', 'crawl'])
  })

  it('ignores heartbeats and malformed payloads instead of inventing events', async () => {
    const { api, es } = await mountEvents()
    es().emit('message', '')
    es().emit('message', 'not json')
    es().emit('message', {})
    es().emit('message', ': ping')
    expect(api.events.value).toEqual([])
  })

  it('keeps failed steps visible and shows backend messages verbatim', async () => {
    const { api, es } = await mountEvents()
    es().emit('message', ev(1, 'crawl_competitor', 'failed'))
    expect(api.events.value[0]?.status).toBe('failed')
    expect(api.events.value[0]?.message).toBe('msg crawl_competitor')
  })

  it('closes the stream on unmount', async () => {
    const { es, wrapper } = await mountEvents()
    const inst = es()
    wrapper.unmount()
    expect(inst.closed).toBe(true)
    expect(setStream).toHaveBeenLastCalledWith('idle')
  })
})

describe('useApproveIntervention (no optimistic UI, UI.md section 55)', () => {
  const refresh = vi.fn()
  const apiFetch = vi.fn()
  beforeEach(() => {
    refresh.mockReset(); apiFetch.mockReset()
    Object.assign(globalThis, { ref, refreshNuxtData: refresh, apiFetch, isAlreadyApplied, toValue: (v: any) => (typeof v === 'function' ? v() : v?.value ?? v) })
  })

  it('refetches from the backend after a successful approval and exposes no local executed state', async () => {
    apiFetch.mockResolvedValue({ incident_state: 'approved' })
    const { useApproveIntervention } = await import('~/composables/useApproveIntervention')
    const c = useApproveIntervention('inc-1')
    expect(Object.keys(c)).not.toContain('executed')
    await c.approve('iv-1', 'ok')
    expect(apiFetch).toHaveBeenCalledWith('/api/interventions/iv-1/approve', { method: 'POST', body: { note: 'ok' } })
    expect(refresh).toHaveBeenCalledWith('incident:inc-1')
    expect(refresh).toHaveBeenCalledWith('incident:inc-1:interventions')
    expect(c.submitting.value).toBe(false)
  })

  it('on a backend refusal (e.g. 409) records the error, does not refetch as if it succeeded, and rethrows', async () => {
    apiFetch.mockRejectedValue({ kind: 'failed', status: 409, message: 'cannot execute while the incident is detected' })
    const { useApproveIntervention } = await import('~/composables/useApproveIntervention')
    const c = useApproveIntervention('inc-1')
    await expect(c.execute('iv-1')).rejects.toBeTruthy()
    expect(c.error.value?.message).toContain('cannot execute')
    expect(refresh).not.toHaveBeenCalled()
    expect(c.submitting.value).toBe(false)
  })

  it('recordExecuted posts snake_case to /executed, omits blanks, and refetches (never optimistic)', async () => {
    apiFetch.mockResolvedValue({ incident_state: 'awaiting_verification' })
    const { useApproveIntervention } = await import('~/composables/useApproveIntervention')
    const c = useApproveIntervention('inc-1')
    await c.recordExecuted('iv-1', { executedAt: '2026-10-02T10:00:00.000Z', referenceUrl: 'https://x.example/p', note: '', actualChange: 'my text' })
    expect(apiFetch).toHaveBeenCalledWith('/api/interventions/iv-1/executed', {
      method: 'POST', body: { executed_at: '2026-10-02T10:00:00.000Z', reference_url: 'https://x.example/p', actual_change: 'my text' }
    })
    expect(refresh).toHaveBeenCalledWith('incident:inc-1')
  })

  it('a repeated recordExecuted (409 already_executed) is treated as already applied: refetch, no error, no throw', async () => {
    apiFetch.mockRejectedValue({ kind: 'failed', status: 409, code: 'CONFLICT', message: 'already executed', details: { reason: 'already_executed' } })
    const { useApproveIntervention } = await import('~/composables/useApproveIntervention')
    const c = useApproveIntervention('inc-1')
    await expect(c.recordExecuted('iv-1', {})).resolves.toBeNull()
    expect(c.error.value).toBeNull()
    expect(c.message.value).toContain('Already recorded')
    expect(refresh).toHaveBeenCalledWith('incident:inc-1')
  })

  it('a genuine 409 refusal (invalid transition) is still surfaced and rethrown', async () => {
    apiFetch.mockRejectedValue({ kind: 'failed', status: 409, code: 'INVALID_STATE_TRANSITION', message: 'rejected is final', details: null })
    const { useApproveIntervention } = await import('~/composables/useApproveIntervention')
    const c = useApproveIntervention('inc-1')
    await expect(c.approve('iv-1')).rejects.toBeTruthy()
    expect(c.error.value?.code).toBe('INVALID_STATE_TRANSITION')
    expect(refresh).not.toHaveBeenCalled()
  })
})
