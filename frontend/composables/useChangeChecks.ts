import type { ChangeCheck, ChangeCheckList, GuardDecision } from '~/types'

export const CHECKS_PAGE_SIZE = 10

/**
 * Paged list of change checks (GET /api/change-checks). Scoped to the current organization, optionally to one experiment
 * (`experiment_code`) and/or a decision. Refetches when the global SSE feed reports change_check.created/decided.
 */
export function useChangeChecks(opts: { experimentCode?: MaybeRefOrGetter<string | null | undefined>; decision?: MaybeRefOrGetter<GuardDecision | '' | null | undefined>; pageSize?: number } = {}) {
  const org = useOrganizationStore()
  const live = useLiveSystemStore()
  const pageSize = opts.pageSize ?? CHECKS_PAGE_SIZE
  const page = ref(0)
  const code = computed(() => toValue(opts.experimentCode) ?? null)
  const decision = computed(() => toValue(opts.decision) || null)
  const res = useApiData<unknown, ChangeCheckList>(
    computed(() => `change-checks:${org.currentId ?? 'none'}:${code.value ?? 'all'}:${decision.value ?? 'any'}:${page.value}`),
    () => apiFetch('/api/change-checks', {
      query: { org_id: org.currentId, experiment_code: code.value, decision: decision.value, limit: pageSize, offset: page.value * pageSize }
    }),
    normChangeCheckList,
    { enabled: () => org.loaded && Boolean(org.currentId) }
  )
  watch([code, decision], () => { page.value = 0 })
  watch(() => live.changeCheckTick, () => {
    if (!res.pending.value && res.error.value?.kind !== 'unavailable') res.refresh()
  })
  const total = computed(() => res.data.value?.total ?? null)
  const hasNext = computed(() => {
    const d = res.data.value
    if (!d) return false
    return d.total != null ? (page.value + 1) * pageSize < d.total : d.items.length === pageSize
  })
  return { ...res, page, pageSize, total, hasNext, next: () => { if (hasNext.value) page.value++ }, prev: () => { if (page.value > 0) page.value-- } }
}

export type { ChangeCheck }
