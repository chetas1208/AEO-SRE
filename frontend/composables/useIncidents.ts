import type { IncidentList } from '~/types'

/** Incident queue for the current organization, scoped by time range. Severity tabs filter client-side over one fetch so counts stay stable. */
export function useIncidents() {
  const org = useOrganizationStore()
  const sel = useIncidentSelectionStore()
  const key = computed(() => `incidents:${org.loaded}:${org.currentId ?? 'none'}:${sel.range}`)
  const res = useApiData<unknown, IncidentList>(
    key,
    () => apiFetch('/api/incidents', { query: { org_id: org.currentId, range: sel.range, limit: 200 } }),
    normIncidentList,
    { enabled: () => org.loaded }
  )
  useAutoRefresh(() => res.refresh(), 30_000)
  return res
}
