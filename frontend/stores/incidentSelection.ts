import { defineStore } from 'pinia'
import type { Severity } from '~/types'

export type SeverityFilter = 'all' | Exclude<Severity, 'low'>
export type TimeRange = '24h' | '7d' | '30d'

export const useIncidentSelectionStore = defineStore('incidentSelection', () => {
  const selectedId = ref<string | null>(null)
  const severity = ref<SeverityFilter>('all')
  const query = ref('')
  const range = ref<TimeRange>('7d')
  const orderedIds = ref<string[]>([])

  function select(id: string | null) { selectedId.value = id }
  function setOrder(ids: string[]) { orderedIds.value = ids }
  function step(delta: 1 | -1): string | null {
    const ids = orderedIds.value
    if (!ids.length) return null
    const i = selectedId.value ? ids.indexOf(selectedId.value) : -1
    const next = ids[Math.min(ids.length - 1, Math.max(0, i + delta))]
    return next ?? null
  }

  return { selectedId, severity, query, range, orderedIds, select, setOrder, step }
})
