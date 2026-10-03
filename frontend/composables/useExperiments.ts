import type { ExperimentCreateIn, ExperimentDetail, ExperimentList } from '~/types'
import { isUuid } from '~/utils/apiBase'

export function useExperiments() {
  const org = useOrganizationStore()
  const res = useApiData<unknown, ExperimentList>(
    computed(() => `experiments:${org.loaded}:${org.currentId ?? 'none'}`),
    () => apiFetch('/api/experiments', { query: { limit: 200 } }),
    normExperimentList,
    { enabled: () => org.loaded }
  )
  useAutoRefresh(() => res.refresh(), 30_000)

  const isCreating = ref(false)
  const createError = ref<string | null>(null)
  const createSuccess = ref<string | null>(null)

  async function createExperiment(payload: ExperimentCreateIn): Promise<ExperimentDetail> {
    isCreating.value = true
    createError.value = null
    createSuccess.value = null

    try {
      const orgId = isUuid(payload.org_id) ? payload.org_id : isUuid(org.currentId) ? org.currentId! : undefined
      const response = await apiFetch<Record<string, unknown>>('/api/experiments', {
        method: 'POST',
        body: {
          ...payload,
          ...(orgId ? { org_id: orgId } : {})
        }
      })
      const detail = normExperimentDetail(response)
      createSuccess.value = `Experiment ${detail.code} successfully created`
      await res.refresh()
      return detail
    } catch (err: any) {
      const message =
        err?.data?.error?.message ||
        err?.response?._data?.error?.message ||
        err?.message ||
        'Failed to create experiment'
      createError.value = message
      throw new Error(message)
    } finally {
      isCreating.value = false
    }
  }

  return {
    ...res,
    isCreating,
    createError,
    createSuccess,
    createExperiment
  }
}
