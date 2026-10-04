import type { GContext, GExplanation, GHealth, GraphView, GraphWindow } from '~/types/graph'
import { DEFAULT_GRAPH_WINDOW, sinceParam } from '~/utils/graph'

// Data hooks for the Neo4j-backed graph API. The browser only talks to the Profound Lift API (never Neo4j). Every hook is scoped to
// the current organization and refetches when the global SSE feed reports `graph_projected`.

type Id = MaybeRefOrGetter<string | null | undefined>

function liveRefresh(refresh: () => unknown) {
  const live = useLiveSystemStore()
  watch(() => live.graphTick, () => refresh())
}

function lineage(kind: 'changes' | 'experiments', id: Id, windowRef: MaybeRefOrGetter<GraphWindow | null> | undefined) {
  const org = useOrganizationStore()
  const win = computed(() => toValue(windowRef) ?? null)
  const key = computed(() => toValue(id) ?? null)
  const res = useApiData<unknown, GraphView | null>(
    computed(() => `graph-lineage:${kind}:${org.currentId ?? 'none'}:${key.value ?? 'none'}:${win.value ?? 'all'}`),
    () => apiFetch(`/api/graph/${kind}/${encodeURIComponent(key.value as string)}/lineage`, { query: { org_id: org.currentId, since: sinceParam(win.value) } }),
    normGraphView,
    { enabled: () => org.loaded && !!key.value }
  )
  // Last good snapshot: kept so an outage/stale state can say "last good graph at <timestamp>" without showing old nodes as current.
  const lastGoodAt = ref<string | null>(null)
  watch(res.data, (v) => { if (v && !v.unavailable && !v.stale && v.generatedAt) lastGoodAt.value = v.generatedAt }, { immediate: true })
  liveRefresh(res.refresh)
  return { ...res, lastGoodAt }
}

/** GET /api/graph/changes/{id}/lineage?since=... */
export function useGraphLineage(changeId: Id, window: MaybeRefOrGetter<GraphWindow | null> = DEFAULT_GRAPH_WINDOW) {
  return lineage('changes', changeId, window)
}

/** GET /api/graph/experiments/{id}/lineage?since=... */
export function useGraphExperimentLineage(experimentId: Id, window: MaybeRefOrGetter<GraphWindow | null> = DEFAULT_GRAPH_WINDOW) {
  return lineage('experiments', experimentId, window)
}

/** GET /api/graph/changes/{id}/context (policy recommendation, graph features, historical neighbours). */
export function useGraphContext(changeId: Id) {
  const org = useOrganizationStore()
  const key = computed(() => toValue(changeId) ?? null)
  const res = useApiData<unknown, GContext | null>(
    computed(() => `graph-context:${org.currentId ?? 'none'}:${key.value ?? 'none'}`),
    () => apiFetch(`/api/graph/changes/${encodeURIComponent(key.value as string)}/context`, { query: { org_id: org.currentId } }),
    normGContext,
    { enabled: () => org.loaded && !!key.value }
  )
  liveRefresh(res.refresh)
  return res
}

/** GET /api/graph/changes/{id}/explanation (deterministic path-to-text statements with supporting node ids). */
export function useGraphExplanation(changeId: Id) {
  const org = useOrganizationStore()
  const key = computed(() => toValue(changeId) ?? null)
  const res = useApiData<unknown, GExplanation | null>(
    computed(() => `graph-explanation:${org.currentId ?? 'none'}:${key.value ?? 'none'}`),
    () => apiFetch(`/api/graph/changes/${encodeURIComponent(key.value as string)}/explanation`, { query: { org_id: org.currentId } }),
    normGExplanation,
    { enabled: () => org.loaded && !!key.value }
  )
  liveRefresh(res.refresh)
  return res
}

/** GET /api/graph/health */
export function useGraphHealth() {
  const org = useOrganizationStore()
  const res = useApiData<unknown, GHealth | null>(
    computed(() => `graph-health:${org.currentId ?? 'none'}`),
    () => apiFetch('/api/graph/health', { query: { org_id: org.currentId } }),
    normGHealth,
    { enabled: () => org.loaded }
  )
  liveRefresh(res.refresh)
  return res
}
