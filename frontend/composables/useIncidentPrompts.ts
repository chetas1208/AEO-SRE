import type { PromptSet } from '~/types'

export function useIncidentPrompts(id: MaybeRefOrGetter<string>) {
  return useApiData<unknown, PromptSet>(
    computed(() => `incident:${toValue(id)}:prompts`),
    () => apiFetch(`/api/incidents/${encodeURIComponent(toValue(id))}/prompts`, { query: { limit: 200 } }),
    normPrompts
  )
}
