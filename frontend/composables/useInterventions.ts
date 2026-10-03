import type { InterventionSet } from '~/types'

export function useInterventions(id: MaybeRefOrGetter<string>) {
  return useApiData<unknown, InterventionSet>(
    computed(() => `incident:${toValue(id)}:interventions`),
    () => apiFetch(`/api/incidents/${encodeURIComponent(toValue(id))}/interventions`),
    normInterventions
  )
}
