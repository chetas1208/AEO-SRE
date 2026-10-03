import type { ExperimentList } from '~/types'

export function useExperiments() {
  const org = useOrganizationStore()
  const res = useApiData<unknown, ExperimentList>(
    computed(() => `experiments:${org.loaded}:${org.currentId ?? 'none'}`),
    () => apiFetch('/api/experiments', { query: { limit: 200 } }),
    normExperimentList,
    { enabled: () => org.loaded }
  )
  useAutoRefresh(() => res.refresh(), 60_000)
  return res
}
