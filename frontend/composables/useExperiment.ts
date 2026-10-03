import type { ExperimentDetail } from '~/types'

export function useExperiment(id: MaybeRefOrGetter<string>) {
  return useApiData<unknown, ExperimentDetail>(
    computed(() => `experiment:${toValue(id)}`),
    () => apiFetch(`/api/experiments/${encodeURIComponent(toValue(id))}`),
    (r) => normExperimentDetail(r as any)
  )
}
