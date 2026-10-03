import { defineStore } from 'pinia'
import type { SystemHealth, ApiErrorInfo } from '~/types'

export type StreamState = 'idle' | 'connecting' | 'connected' | 'disconnected'
export type GlobalStreamState = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected'

const HEARTBEAT_STALE_MS = 25_000 // server heartbeats every 10s; 2.5 missed ticks = stale
const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000, 30000]
const SEEN_MAX = 512

export const useLiveSystemStore = defineStore('liveSystem', () => {
  const health = ref<SystemHealth | null>(null)
  const apiReachable = ref<boolean | null>(null)
  const error = ref<ApiErrorInfo | null>(null)
  const fetchedAt = ref<string | null>(null)
  const stream = ref<StreamState>('idle')

  async function refresh() {
    try {
      const [h, c] = await Promise.allSettled([apiFetch('/api/health'), apiFetch('/api/system/capabilities')])
      if (h.status === 'rejected' && c.status === 'rejected') throw h.reason
      health.value = normHealth(h.status === 'fulfilled' ? h.value : null, c.status === 'fulfilled' ? c.value : null)
      apiReachable.value = true
      error.value = null
    } catch (e) {
      apiReachable.value = false
      error.value = e as ApiErrorInfo
    } finally {
      fetchedAt.value = new Date().toISOString()
    }
  }

  function setStream(s: StreamState) { stream.value = s }

  // ---- global feed: GET /api/events (named `heartbeat` every 10s + `incident_event`) -------------------------------
  const globalStream = ref<GlobalStreamState>('idle')
  const lastHeartbeatAt = ref<number | null>(null)
  const now = ref(Date.now())
  const incidentEventTick = ref(0) // bumps per NEW incident_event; pages watch it to refetch lists
  const lastIncidentEvent = ref<Record<string, any> | null>(null)
  const changeCheckTick = ref(0) // bumps on change_check.created / change_check.decided; the Change checks feed refetches
  const reconnectAttempt = ref(0)
  const seen = new Set<string>()
  let es: EventSource | null = null
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  let tickTimer: ReturnType<typeof setInterval> | undefined
  let wanted = false

  const heartbeatAgeSeconds = computed(() => lastHeartbeatAt.value == null ? null : Math.max(0, Math.round((now.value - lastHeartbeatAt.value) / 1000)))

  function onHeartbeat() { lastHeartbeatAt.value = Date.now(); now.value = Date.now() }

  function onIncidentEvent(raw: string) {
    let data: any
    try { data = camelize(JSON.parse(raw)) } catch { return }
    if (!data || typeof data !== 'object') return
    const id = data.id != null ? String(data.id) : null
    if (id) { // dedupe: the same event can arrive via local + redis delivery or after a reconnect
      if (seen.has(id)) return
      seen.add(id)
      if (seen.size > SEEN_MAX) seen.delete(seen.values().next().value as string)
    }
    lastIncidentEvent.value = data
    incidentEventTick.value++
    if (typeof data.eventType === 'string' && data.eventType.startsWith('change_check.')) changeCheckTick.value++
  }

  /** Named SSE events carrying change_check.* (small payloads); the tick is all the feed needs, it refetches the list. */
  function onChangeCheckEvent() { changeCheckTick.value++ }

  function scheduleReconnect() {
    if (!wanted) return
    globalStream.value = 'reconnecting'
    const delay = BACKOFF_MS[Math.min(reconnectAttempt.value, BACKOFF_MS.length - 1)]
    reconnectAttempt.value++
    clearTimeout(retryTimer)
    retryTimer = setTimeout(openGlobal, delay)
  }

  function openGlobal() {
    es?.close()
    es = null
    if (typeof EventSource === 'undefined') { globalStream.value = 'disconnected'; return }
    if (globalStream.value === 'idle') globalStream.value = 'connecting'
    const src = new EventSource(`${useApiBase()}/api/events`)
    es = src
    src.onopen = () => {
      const wasReconnect = reconnectAttempt.value > 0
      globalStream.value = 'connected'
      reconnectAttempt.value = 0
      onHeartbeat()
      if (wasReconnect) { incidentEventTick.value++; changeCheckTick.value++ } // no replay on this feed: refetch lists after a gap
    }
    src.onerror = () => { src.close(); if (es === src) es = null; scheduleReconnect() } // own backoff, not EventSource's fixed retry
    src.addEventListener('heartbeat', onHeartbeat)
    src.addEventListener('incident_event', (m) => onIncidentEvent((m as MessageEvent).data))
    for (const name of ['change_check.created', 'change_check.decided', 'change_check']) src.addEventListener(name, onChangeCheckEvent)
  }

  /** Idempotent. Client only. */
  function startGlobalStream() {
    if (typeof window === 'undefined' || wanted) return
    wanted = true
    reconnectAttempt.value = 0
    globalStream.value = 'connecting'
    openGlobal()
    tickTimer = setInterval(() => {
      now.value = Date.now()
      if (globalStream.value === 'connected' && lastHeartbeatAt.value != null && now.value - lastHeartbeatAt.value > HEARTBEAT_STALE_MS) {
        es?.close(); es = null; scheduleReconnect() // connection looks open but is silent
      }
    }, 1000)
  }

  function stopGlobalStream() {
    wanted = false
    clearTimeout(retryTimer)
    clearInterval(tickTimer)
    es?.close()
    es = null
    globalStream.value = 'idle'
    lastHeartbeatAt.value = null
  }

  /** Live pill: never green unless the API answered and any open event stream is connected. */
  const liveState = computed<'live' | 'degraded' | 'reconnecting' | 'disconnected'>(() => {
    if (apiReachable.value !== true) return 'disconnected'
    if (globalStream.value === 'reconnecting') return 'reconnecting'
    if (globalStream.value === 'disconnected') return 'disconnected'
    const caps = health.value?.capabilities ?? []
    if (caps.some((c) => c.state !== 'healthy')) return 'degraded'
    return 'live'
  })

  return {
    health, apiReachable, error, fetchedAt, stream, liveState, refresh, setStream,
    globalStream, lastHeartbeatAt, heartbeatAgeSeconds, incidentEventTick, changeCheckTick, lastIncidentEvent, reconnectAttempt,
    startGlobalStream, stopGlobalStream
  }
})
