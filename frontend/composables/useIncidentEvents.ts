import type { IncidentEvent } from '~/types'
import type { StreamState } from '~/stores/liveSystem'

/** Server event_types that mean persisted state changed: the page refetches on these (or on failed/warning steps). */
const STATE_CHANGING = new Set([
  'hypothesis.confirmed', 'policy.completed', 'approval.requested', 'experiment.activated',
  'verification.scheduled', 'verification.completed', 'reward.created'
])

const NAMED = ['message', 'step', 'progress', 'investigation', 'notification', 'incident_event', 'stage']

/** SSE stream for one incident (GET /api/incidents/:id/events). Failed steps are retained; messages are shown as sent. */
export function useIncidentEvents(id: MaybeRefOrGetter<string>, onTerminal?: () => void) {
  const live = useLiveSystemStore()
  const events = ref<IncidentEvent[]>([])
  const state = ref<StreamState>('idle')
  let es: EventSource | null = null

  function close() {
    es?.close()
    es = null
  }

  function push(raw: string) {
    let data: any
    try { data = camelize(JSON.parse(raw)) } catch { return }
    if (!data || typeof data !== 'object' || !data.stage) return // heartbeat / non-event payload
    const ev = normEvent(data)
    if (events.value.some((e) => e.id === ev.id)) return
    events.value = [...events.value, ev].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp))
    const changed = (ev.eventType != null && STATE_CHANGING.has(ev.eventType)) || ev.status === 'success'
    if (onTerminal && (changed || ev.status === 'failed' || ev.status === 'warning')) onTerminal()
  }

  function open() {
    close()
    if (typeof EventSource === 'undefined') return
    state.value = 'connecting'
    live.setStream('connecting')
    es = new EventSource(`${useApiBase()}/api/incidents/${encodeURIComponent(toValue(id))}/events`)
    es.onopen = () => { state.value = 'connected'; live.setStream('connected') }
    es.onerror = () => { state.value = 'disconnected'; live.setStream('disconnected') } // EventSource auto-reconnects
    for (const name of NAMED) es.addEventListener(name, (m) => push((m as MessageEvent).data))
  }

  onMounted(() => open())
  watch(() => toValue(id), () => { events.value = []; open() })
  onBeforeUnmount(() => { close(); live.setStream('idle') })

  return { events, state, reconnect: open }
}
