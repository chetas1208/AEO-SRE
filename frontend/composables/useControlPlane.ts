import type { ControlPlaneResponse, ApiErrorInfo } from '~/types'
import { isBrowserApiMisconfigured } from '~/utils/apiBase'
import { apiFetch, toApiError, useAutoRefresh, useApiBase } from './useApi'

export function useControlPlane() {
  const data = useState<ControlPlaneResponse | null>('control-plane-data', () => null)
  const isLoading = ref(false)
  const error = ref<ApiErrorInfo | null>(null)
  let es: EventSource | null = null

  async function load() {
    isLoading.value = true
    error.value = null
    try {
      const res = await apiFetch<ControlPlaneResponse>('/api/control-plane')
      data.value = res
    } catch (e) {
      error.value = toApiError(e)
    } finally {
      isLoading.value = false
    }
  }

  function setupSSE() {
    if (typeof EventSource === 'undefined') return
    const config = useRuntimeConfig()
    if (config.public.apiSameOrigin) return
    if (isBrowserApiMisconfigured()) {
      error.value = {
        kind: 'unavailable',
        message: 'API is misconfigured for production (localhost). Set NUXT_PUBLIC_API_BASE_URL on Vercel and redeploy.',
      }
      return
    }
    if (es) {
      es.close()
      es = null
    }
    const base = useApiBase()
    try {
      es = new EventSource(`${base}/api/control-plane/events`)
      es.onmessage = (_e) => {
        // Upon receiving any control plane event, refresh the aggregated state
        load()
      }
      es.addEventListener('agent.activity', () => load())
      es.addEventListener('experiment.created', () => load())
      es.addEventListener('decision.reviewed', () => load())
      es.onerror = () => {
        // EventSource will auto reconnect
      }
    } catch (err) {
      // Ignore SSE init failure in restricted browser contexts
    }
  }

  onMounted(() => {
    load()
    setupSSE()
  })

  onBeforeUnmount(() => {
    if (es) {
      es.close()
      es = null
    }
  })

  // Periodically refresh every 15s when active tab
  useAutoRefresh(load, 15000)

  return {
    data,
    isLoading,
    error,
    refresh: load
  }
}
