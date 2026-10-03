import type { ApiErrorInfo, EvidenceItem, GraphData, Hypothesis } from '~/types'

export function useIncidentEvidence(id: MaybeRefOrGetter<string>) {
  return useApiData<unknown, EvidenceItem[]>(
    computed(() => `incident:${toValue(id)}:evidence`),
    () => apiFetch(`/api/incidents/${encodeURIComponent(toValue(id))}/evidence`),
    (r) => unwrapList(r, 'evidence').map(normEvidence)
  )
}

export function useIncidentHypotheses(id: MaybeRefOrGetter<string>) {
  return useApiData<unknown, Hypothesis[]>(
    computed(() => `incident:${toValue(id)}:hypotheses`),
    () => apiFetch(`/api/incidents/${encodeURIComponent(toValue(id))}/hypotheses`),
    (r) => unwrapList(r, 'hypotheses').map(normHypothesis).sort((a, b) => b.confidence - a.confidence)
  )
}

export function useIncidentGraph(id: MaybeRefOrGetter<string>) {
  return useApiData<unknown, GraphData>(
    computed(() => `incident:${toValue(id)}:graph`),
    () => apiFetch(`/api/incidents/${encodeURIComponent(toValue(id))}/graph`),
    normGraph
  )
}

/** Lazy per-row evidence detail (GET /evidence/{id}); the list endpoint omits the verbatim excerpt. */
export function useEvidenceDetail(incidentId: MaybeRefOrGetter<string>) {
  const detail = ref<Record<string, EvidenceItem>>({})
  const errors = ref<Record<string, ApiErrorInfo>>({})
  const loading = ref<Record<string, boolean>>({})

  async function load(evidenceId: string) {
    if (detail.value[evidenceId] || loading.value[evidenceId]) return
    loading.value = { ...loading.value, [evidenceId]: true }
    try {
      const raw = await apiFetch(`/api/incidents/${encodeURIComponent(toValue(incidentId))}/evidence/${encodeURIComponent(evidenceId)}`)
      detail.value = { ...detail.value, [evidenceId]: normEvidence(raw as any) }
      const { [evidenceId]: _drop, ...rest } = errors.value
      errors.value = rest
    } catch (e) {
      errors.value = { ...errors.value, [evidenceId]: e as ApiErrorInfo }
    } finally {
      loading.value = { ...loading.value, [evidenceId]: false }
    }
  }
  return { detail, errors, loading, load }
}
