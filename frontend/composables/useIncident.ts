import type { IncidentDetail } from '~/types'

export function useIncident(id: MaybeRefOrGetter<string>) {
  return useApiData<unknown, IncidentDetail>(
    computed(() => `incident:${toValue(id)}`),
    () => apiFetch(`/api/incidents/${encodeURIComponent(toValue(id))}`),
    (r) => normIncidentDetail(r as any)
  )
}

export async function investigateIncident(id: string) {
  return apiFetch(`/api/incidents/${encodeURIComponent(id)}/investigate`, { method: 'POST' })
}

export async function resolveIncident(id: string) {
  return apiFetch(`/api/incidents/${encodeURIComponent(id)}/resolve`, { method: 'POST' })
}
